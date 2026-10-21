#!/usr/bin/env python3
"""Import the DUT inverters submitted through the Google Form into workshop_inverter.gds.

For every form response (お名前 / 連絡先メールアドレス / シークレットキー / GitHub run URL):
  1. The run (https://github.com/<owner>/<repo>/actions/runs/<RUN_ID>) is looked up with the
     GitHub API and src/inv_dut_NNN.gds is downloaded at the commit the run checked.
  2. シークレットキー must equal the secret_key of DUT_NUM NNN in dut_params_secretkey.csv.
  3. The same Pre-check / DRC / LVS as ~/template_inverter/RISC-V-Day_MPW_template (its CI and
     scripts/run_local.sh) is run with the ishi-kai gf180mcuD decks (~/template_inverter/gf180mcuD),
     plus the frame check of ~/workshop_inverter/layout/replace_dut.py.
  4. The DUTs that pass are put into a copy of the pristine top layout with replace_dut.py
     (which also rewrites the LVS netlist .cdl), and ~/workshop_inverter/layout/check_top.py runs
     the ~/pdk/gf180mcuD DRC (all decks except density) and LVS on it.  If the top fails, the
     DUTs responsible are found (DRC markers by location, otherwise by bisection), dropped and
     the top is rebuilt and checked again.
  5. A clean top is written to workshop_inverter.gds / .cdl (the previous ones are backed up).
Rejected submissions (key mismatch, download / DRC / LVS errors) are not imported; the report
(reports/report_<time>.md / .csv) lists them with name and e-mail address.

Meant to run as a batch job (run_batch.sh, e.g. every 30 minutes from cron):
  - What has been processed is kept in state/state.json: every handled response (identified by a
    hash of its contents) with its result, and the imported DUTs, whose GDS are kept in
    state/duts/.  Each run only handles the responses not in the state; with none, it exits.
  - The top is rebuilt from the pristine top layout (base/workshop_inverter_base.gds, copied from
    workshop_inverter.gds on the first run) + the DUTs imported before + the new ones; only the
    new DUTs are suspected when the top check fails.
  - A newer passing response for a DUT replaces the imported one.
  - Temporary problems (GitHub rate limit / network, a DRC / LVS that did not run) leave the
    response unprocessed so that the next run retries it (up to MAX_TRIES times).
  - A run exits at once while another one still holds state/lock.
  - --reset forgets everything processed (state/, the DUT check cache) and puts the pristine top
    back into workshop_inverter.gds / .cdl (the previous ones are backed up), so that every
    response is processed again.

Publishing (--publish-repo URL, e.g. git@github.com:ishi-kai/RISC-V-Day_2611_inverter_workshop.git):
  a report per processed response (NNN_<time>.md: no name, e-mail address or
  secret key) is queued in state/outbox/, then the working copy publish/repo is synced with the
  remote, workshop_inverter.gds / .cdl go to GDS/, a layout image to images/workshop_inverter.png
  (shown in README.md), the DUT map coloured by status (dut_map.py) to images/dut_map.png / .svg, the queued reports to report/, and it is committed and pushed.  A failed
  push is retried by the next run (also when there are no new responses).  The SSH key is
  $SSH_KEY (default ~/.ssh/id_ed25519), its passphrase $SSH_KEY_PASSPHRASE.

Form responses come from one of:
  --responses FILE.csv      the responses spreadsheet downloaded as CSV
                            (Google Sheets: ファイル > ダウンロード > カンマ区切り形式 (.csv))
  --sheet-id ID             the responses spreadsheet read with the Sheets API as a service
                            account (--credentials KEY.json or $GOOGLE_APPLICATION_CREDENTIALS;
                            needs `pip install google-auth requests`); see README.md
GitHub: works without a token for public repositories (60 API requests / hour);
set $GITHUB_TOKEN to raise the limit or to read private repositories.

Usage:  python3 import_submissions.py (--responses FILE.csv | --sheet-id ID [--range A1:Z])
            [--credentials KEY.json] [--threads N] [--dry-run] [--no-top-check]
            [--publish-repo URL [--publish-branch main]] [--reset]
        python3 import_submissions.py --reset          (reset only)
  --dry-run       download and check the new DUTs only; write neither the top layout nor the state
  --no-top-check  build and write the top layout without the top-level DRC / LVS (not advised)
"""
import argparse
import fcntl
import csv
import datetime
import hashlib
import hmac
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser("~")
TEMPLATE = os.environ.get("INV_TEMPLATE", os.path.join(HOME, "template_inverter/RISC-V-Day_MPW_template"))
TEMPLATE_PDK = os.environ.get("INV_TEMPLATE_PDK", os.path.join(HOME, "template_inverter/gf180mcuD"))
WORKSHOP_LAYOUT = os.environ.get("INV_WORKSHOP_LAYOUT", os.path.join(HOME, "workshop_inverter/layout"))
TOP = "workshop_inverter"
TOP_GDS = os.path.join(HERE, f"{TOP}.gds")
TOP_CDL = os.path.join(HERE, f"{TOP}.cdl")
BASE_DIR = os.path.join(HERE, "base")
BASE_GDS = os.path.join(BASE_DIR, f"{TOP}_base.gds")
KEY_CSV = os.path.join(HERE, "dut_params_secretkey.csv")
RUN_URL_RE = re.compile(r"https?://github\.com/([\w.-]+)/([\w.-]+)/actions/runs/(\d+)")
DUT_RE = re.compile(r"^inv_dut_(\d{3})\.gds$")
STATE_DIR = os.path.join(HERE, "state")
STATE_JSON = os.path.join(STATE_DIR, "state.json")
OUTBOX = os.path.join(STATE_DIR, "outbox")
PUBLISH_DIR = os.path.join(HERE, "publish", "repo")
LYP = os.environ.get("INV_IMAGE_LYP", os.path.join(HERE, "gf180mcu.lyp"))   # layout image colours
IMAGE_SIZE = (1190, 1685)   # same size as the original images/workshop_inverter.png
MAX_TRIES = 3      # runs a response with temporary problems is retried before it is rejected
DUT_MARGIN = 1.0   # um around a DUT frame within which a top-level DRC marker blames that DUT

# Form question titles (matched by prefix; the CSV export adds タイムスタンプ in front)
COL_TIME, COL_NAME, COL_MAIL, COL_KEY, COL_URL = "タイムスタンプ", "お名前", "連絡先メールアドレス", "シークレットキー", "github"


def log(msg):
    print(msg, flush=True)


# ---------------------------------------------------------------- form responses
def read_response_rows(args):
    """Return the responses as a list of rows (first row = header)."""
    if args.responses:
        with open(args.responses, newline="", encoding="utf-8-sig") as f:
            return list(csv.reader(f))
    try:
        from google.auth.transport.requests import AuthorizedSession
        from google.oauth2 import service_account
    except ImportError:
        sys.exit("--sheet-id needs google-auth: pip install google-auth requests")
    key = args.credentials or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not key:
        sys.exit("--sheet-id needs --credentials KEY.json or $GOOGLE_APPLICATION_CREDENTIALS")
    creds = service_account.Credentials.from_service_account_file(
        key, scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
    r = AuthorizedSession(creds).get(
        f"https://sheets.googleapis.com/v4/spreadsheets/{args.sheet_id}/values/{args.range}")
    if r.status_code != 200:
        sys.exit(f"Sheets API error {r.status_code}: {r.text[:500]}\n"
                 f"(is the spreadsheet shared with {creds.service_account_email}?)")
    return r.json().get("values", [])


def parse_responses(rows):
    if not rows:
        sys.exit("no form responses")
    header = [h.strip() for h in rows[0]]

    def col(prefix):
        for i, h in enumerate(header):
            if h.lower().startswith(prefix.lower()):
                return i
        return None

    idx = {k: col(k) for k in (COL_TIME, COL_NAME, COL_MAIL, COL_KEY, COL_URL)}
    missing = [k for k, i in idx.items() if i is None and k != COL_TIME]
    if missing:
        sys.exit("responses: missing column(s) " + ", ".join(missing) + f" (header: {header})")
    subs = []
    for n, row in enumerate(rows[1:], start=2):
        get = lambda k: row[idx[k]].strip() if idx[k] is not None and idx[k] < len(row) else ""
        if not any(c.strip() for c in row):
            continue
        subs.append({"row": n, "time": get(COL_TIME), "name": get(COL_NAME), "mail": get(COL_MAIL),
                     "key": get(COL_KEY), "url": get(COL_URL), "dut": None, "status": None,
                     "reason": "", "gds": None})
    return subs


def read_keys(path):
    with open(path, newline="") as f:
        reader = csv.DictReader(f, skipinitialspace=True)
        reader.fieldnames = [c.strip().upper() for c in reader.fieldnames]
        return {int(r["DUT_NUM"]): r["SECRET_KEY"].strip() for r in reader if r.get("DUT_NUM")}


def response_id(s):
    """Stable ID of a form response (row numbers change when responses are deleted)."""
    text = "\x1f".join(s[k] for k in ("time", "name", "mail", "key", "url"))
    return hashlib.sha256(text.encode()).hexdigest()[:16]


class TransientError(Exception):
    """A problem that may go away by itself (rate limit, network, tool crash): retry later."""


# ---------------------------------------------------------------- GitHub
def http_get(url, accept="application/vnd.github+json"):
    req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "import_submissions"})
    token = os.environ.get("GITHUB_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code in (403, 429) or e.code >= 500:   # 403 = rate limit on the GitHub API
            raise TransientError(f"HTTP {e.code} ({url})")
        raise
    except (urllib.error.URLError, OSError) as e:
        raise TransientError(f"{e} ({url})")


def download_gds(sub, dl_dir):
    """Download src/inv_dut_NNN.gds of the run's commit; return (path, dut_num, info)."""
    m = RUN_URL_RE.search(sub["url"])
    if not m:
        raise ValueError(f"GitHub の RUN URL として読めません: '{sub['url']}'")
    owner, repo, run_id = m.groups()
    api = f"https://api.github.com/repos/{owner}/{repo}"
    try:
        run = json.loads(http_get(f"{api}/actions/runs/{run_id}"))
    except urllib.error.HTTPError as e:
        raise ValueError(f"RUN {owner}/{repo} #{run_id} を取得できません (HTTP {e.code})")
    sha = run["head_sha"]
    head_repo = (run.get("head_repository") or {}).get("full_name") or f"{owner}/{repo}"
    info = f"{head_repo}@{sha[:10]} (run {run.get('status')}/{run.get('conclusion')})"
    try:
        entries = json.loads(http_get(f"https://api.github.com/repos/{head_repo}/contents/src?ref={sha}"))
    except urllib.error.HTTPError as e:
        raise ValueError(f"{head_repo}@{sha[:10]} の src/ を取得できません (HTTP {e.code})")
    gds = [e for e in entries if e.get("type") == "file" and e["name"].lower().endswith(".gds")]
    if len(gds) != 1 or not DUT_RE.match(gds[0]["name"]):
        names = ", ".join(e["name"] for e in gds) or "なし"
        raise ValueError(f"src/ に inv_dut_NNN.gds が 1 つだけ必要です (見つかったもの: {names})")
    name = gds[0]["name"]
    out = os.path.join(dl_dir, f"run{run_id}", name)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    data = http_get(gds[0]["download_url"], accept="application/octet-stream")
    with open(out, "wb") as f:
        f.write(data)
    return out, int(DUT_RE.match(name).group(1)), info


# ---------------------------------------------------------------- DUT checks (template flow)
def run_logged(cmd, logfile, cwd=None):
    with open(logfile, "w") as f:
        p = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, cwd=cwd)
    return p.returncode, open(logfile, errors="replace").read()


def drc_violations(lyrdb):
    """{category: count} of a KLayout DRC report."""
    import klayout.rdb as rdb
    r = rdb.ReportDatabase("drc")
    r.load(lyrdb)
    return {c.name(): c.num_items() for c in r.each_category() if c.num_items()}


def frame_check(gds):
    """replace_dut.py's frame / pin-label check against the pristine top."""
    import klayout.db as db
    import replace_dut
    tgt = db.Layout()
    tgt.read(BASE_GDS)
    try:
        _, _, _, problems = replace_dut.replace(tgt, gds)
    except SystemExit as e:
        problems = [str(e)]
    return problems


def check_dut(gds, num, work, threads):
    """Pre-check / DRC / LVS as in the template CI; return a list of problems (empty = pass)."""
    top = f"inv_dut_{num:03d}"
    tech = os.path.join(TEMPLATE_PDK, "libs.tech/klayout/tech")
    cdl = os.path.join(TEMPLATE, "LVS", f"{top}.cdl")
    os.makedirs(work, exist_ok=True)
    if not os.path.exists(cdl):
        return [f"DUT 番号が無効です (LVS/{top}.cdl がありません)"]

    rc, out = run_logged([sys.executable, os.path.join(TEMPLATE, "scripts/pre_check.py"),
                          "--top", top, gds], os.path.join(work, "pre_check.log"))
    if rc:
        errs = [l.strip() for l in out.splitlines() if "ERROR" in l or "Error" in l]
        return ["Pre-check: " + ("; ".join(errs) or f"failed (rc={rc}, {work}/pre_check.log)")]

    problems = []
    report = os.path.join(work, f"{top}.lyrdb")
    rc, _ = run_logged(["klayout", "-b", "-r", os.path.join(tech, "drc/gf180mcu.drc"),
                        "-rd", f"input={gds}", "-rd", f"topcell={top}", "-rd", f"report={report}",
                        "-rd", "variant=gf180mcuD", "-rd", "decks=all,-density,inv_dut",
                        "-rd", "run_mode=deep", "-rd", f"threads={threads}"],
                       os.path.join(work, "drc.log"))
    if not os.path.exists(report):
        raise TransientError(f"DRC が実行できませんでした (rc={rc}, {work}/drc.log)")
    else:
        v = drc_violations(report)
        if v:
            problems.append("DRC エラー: " + ", ".join(f"{k} x{n}" for k, n in sorted(v.items())))

    rc, out = run_logged(["klayout", "-b", "-r", os.path.join(tech, "lvs/gf180mcu.lvs"),
                          "-rd", f"input={gds}", "-rd", f"topcell={top}", "-rd", f"schematic={cdl}",
                          "-rd", f"report={os.path.join(work, top + '.lvsdb')}",
                          "-rd", f"target_netlist={os.path.join(work, top + '.extracted')}",
                          "-rd", "metal_top=11K", "-rd", "metal_level=5LM", "-rd", "mim_option=B",
                          "-rd", "run_mode=deep", "-rd", "combine=true"],
                         os.path.join(work, "lvs.log"))
    if "don't match" in out:
        problems.append("LVS 不一致")
    elif "Congratulations! Netlists match." not in out:
        raise TransientError(f"LVS が実行できませんでした (rc={rc}, {work}/lvs.log)")

    problems += ["フレーム: " + p for p in frame_check(gds)]
    return problems


# ---------------------------------------------------------------- top layout
def build_top(gds_list, out_gds):
    """Base layout + the given DUT GDS files -> out_gds / .cdl (replace_dut.py)."""
    os.makedirs(os.path.dirname(out_gds), exist_ok=True)
    shutil.copy(BASE_GDS, out_gds)
    cmd = [sys.executable, os.path.join(WORKSHOP_LAYOUT, "replace_dut.py"), "--gds", out_gds,
           "--csv", KEY_CSV] + gds_list
    if not gds_list:   # nothing to replace: just write the netlist
        cmd = [sys.executable, os.path.join(WORKSHOP_LAYOUT, "gen_lvs_netlist.py"), "--gds", out_gds,
               "--csv", KEY_CSV, os.path.splitext(out_gds)[0] + ".cdl"]
    rc, out = run_logged(cmd, os.path.join(os.path.dirname(out_gds), "replace_dut.log"))
    if rc:
        raise RuntimeError(f"replace_dut.py failed:\n{out}")


def check_top(gds, threads, drc=True, lvs=True):
    """check_top.py on gds -> (drc_ok, lvs_ok); None for a check not run."""
    cmd = [sys.executable, os.path.join(WORKSHOP_LAYOUT, "check_top.py"), "--gds", gds,
           "--threads", str(threads)]
    if not drc:
        cmd.append("--no-drc")
    if not lvs:
        cmd.append("--no-lvs")
    _, out = run_logged(cmd, os.path.join(os.path.dirname(gds), "check_top.log"))
    drc_ok = ("DRC clean" in out) if drc else None
    lvs_ok = ("LVS match" in out) if lvs else None
    return drc_ok, lvs_ok, out


def dut_frames():
    """{dut_num: DBox of its frame in top coordinates} from the base layout."""
    import klayout.db as db
    ly = db.Layout()
    ly.read(BASE_GDS)
    top = ly.cell(TOP)
    frames = {}
    it = top.begin_instances_rec()
    while not it.at_end():
        m = re.fullmatch(r"inv_dut_(\d{3})", it.inst_cell().name)
        if m:
            frames[int(m.group(1))] = it.inst_cell().dbbox().transformed(it.dtrans() * it.inst_dtrans())
        it.next()
    return frames


def drc_culprits(lyrdb, candidates):
    """Split the top-level DRC markers into {dut: [rules]} and the unattributed rules."""
    import klayout.db as db
    import klayout.rdb as rdb
    frames = {n: b.enlarged(DUT_MARGIN, DUT_MARGIN) for n, b in dut_frames().items() if n in candidates}
    r = rdb.ReportDatabase("drc")
    r.load(lyrdb)
    blamed, other = {}, set()
    for item in r.each_item():
        rule = r.category_by_id(item.category_id()).name()
        boxes = []
        for v in item.each_value():
            for kind in ("box", "polygon", "path", "edge", "edge_pair"):
                if getattr(v, f"is_{kind}")():
                    g = getattr(v, kind)()
                    boxes.append(g if isinstance(g, db.DBox) else g.bbox())
        hit = {n for n, f in frames.items() for b in boxes if f.overlaps(b) or f.contains(b.center())}
        for n in hit:
            blamed.setdefault(n, set()).add(rule)
        if not hit:
            other.add(rule)
    return blamed, other


def bisect_bad(cands, test):
    """DUTs whose addition makes test() fail (test(set) -> True = pass)."""
    if len(cands) == 1:
        return set(cands)
    half = len(cands) // 2
    a, b = cands[:half], cands[half:]
    bad = set()
    for part in (a, b):
        if not test(part):
            bad |= bisect_bad(part, test)
    return bad or set(cands)   # neither half fails alone: blame the pair of halves


# ---------------------------------------------------------------- report
STATUS_JA = {"imported": "取り込み済み", "superseded": "同じ DUT の新しい投稿で置き換え",
             "key_mismatch": "シークレットキー不一致", "download_error": "ダウンロード失敗",
             "dut_check_error": "DUT の DRC/LVS エラー", "top_check_error": "統合後の DRC/LVS エラー",
             "checked": "チェック合格 (未統合)", "retry": "一時的なエラー (次回再実行)"}
REJECTED = ("key_mismatch", "download_error", "dut_check_error", "top_check_error")


def write_report(subs, report_dir, stamp, notes):
    os.makedirs(report_dir, exist_ok=True)
    base = os.path.join(report_dir, f"report_{stamp}")
    with open(base + ".csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["row", "timestamp", "name", "email", "dut", "url", "status", "reason"])
        for s in subs:
            w.writerow([s["row"], s["time"], s["name"], s["mail"],
                        "" if s["dut"] is None else f"{s['dut']:03d}", s["url"],
                        s["status"], s["reason"]])
    bad = [s for s in subs if s["status"] in REJECTED]
    lines = [f"# DUT 取り込みレポート ({stamp})", ""]
    lines += [f"- {n}" for n in notes] + [""]
    lines += [f"## 問題のある投稿 ({len(bad)} 件、取り込んでいません)", ""]
    if bad:
        lines += ["| 行 | タイムスタンプ | お名前 | 連絡先メールアドレス | DUT | 内容 | 詳細 |",
                  "|---|---|---|---|---|---|---|"]
        for s in bad:
            dut = "" if s["dut"] is None else f"{s['dut']:03d}"
            reason = s["reason"].replace("|", "\\|").replace("\n", " ")
            lines.append(f"| {s['row']} | {s['time']} | {s['name']} | {s['mail']} | {dut} | "
                         f"{STATUS_JA[s['status']]} | {reason} |")
    else:
        lines.append("なし")
    lines += ["", "## 今回処理した投稿", "", "| 行 | タイムスタンプ | お名前 | 連絡先メールアドレス | DUT | 状態 | URL |",
              "|---|---|---|---|---|---|---|"]
    for s in subs:
        dut = "" if s["dut"] is None else f"{s['dut']:03d}"
        lines.append(f"| {s['row']} | {s['time']} | {s['name']} | {s['mail']} | {dut} | "
                     f"{STATUS_JA[s['status']]} | {s['url']} |")
    with open(base + ".md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return base + ".md", base + ".csv"


def write_public_report(s, stamp, out_dir):
    """Report of one response for the public repository: no name, e-mail address or secret key."""
    os.makedirs(out_dir, exist_ok=True)
    num = "unknown" if s["dut"] is None else f"{s['dut']:03d}"
    path = os.path.join(out_dir, f"{num}_{stamp}.md")
    n = 2
    while os.path.exists(path):   # several responses for the same DUT in one run
        path = os.path.join(out_dir, f"{num}_{stamp}_{n}.md")
        n += 1
    st = s["status"]
    key = "未確認" if st == "download_error" else ("不一致" if st == "key_mismatch" else "OK")
    dut_chk = {"download_error": "未実行", "key_mismatch": "未実行", "dut_check_error": "エラー"}.get(st, "OK")
    top_chk = {"imported": "OK", "top_check_error": "エラー"}.get(st, "未実行")
    created = datetime.datetime.strptime(stamp.rstrip("_"), "%Y%m%d_%H%M%S")
    lines = [f"# inv_dut_{num} 取り込みレポート", "",
             f"- 作成日時: {created:%Y-%m-%d %H:%M:%S}",
             f"- フォームの回答日時: {s['time']}",
             f"- GitHub RUN: {s['url']}",
             f"- 結果: **{STATUS_JA[st]}**", "",
             "| チェック | 結果 |", "|---|---|",
             f"| シークレットキー | {key} |",
             f"| DUT の Pre-check / DRC / LVS (テンプレートと同じ) | {dut_chk} |",
             f"| 統合後の workshop_inverter.gds の DRC (密度以外) / LVS | {top_chk} |", "",
             "## 詳細", "", s["reason"] or "-"]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


def write_status(state, path):
    """Cumulative status of every processed response (state/state.json as CSV)."""
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["id", "timestamp", "name", "email", "dut", "url", "status", "reason", "processed_at"])
        for rid, r in state["responses"].items():
            w.writerow([rid, r["time"], r["name"], r["mail"],
                        "" if r["dut"] is None else f"{r['dut']:03d}", r["url"],
                        r["status"], r["reason"], r["processed_at"]])


# ---------------------------------------------------------------- publishing
def render_image(gds, png):
    import klayout.lay as lay
    view = lay.LayoutView()
    view.load_layout(gds, 0)
    if os.path.exists(LYP):
        view.load_layer_props(LYP)   # without it: KLayout's own palette
    view.set_config("text-visible", "false")   # thousands of std-cell pin labels blot out the decoder
    view.max_hier()
    view.zoom_fit()
    view.save_image(png, *IMAGE_SIZE)


def git(args, cwd=None, check=True):
    """git with the publishing SSH key; the passphrase comes from $SSH_KEY_PASSPHRASE."""
    env = dict(os.environ)
    key = os.path.expanduser(os.environ.get("SSH_KEY", "~/.ssh/id_ed25519"))
    env["GIT_SSH_COMMAND"] = (f"ssh -i '{key}' -o IdentitiesOnly=yes -o BatchMode=no "
                              "-o StrictHostKeyChecking=accept-new")
    env["SSH_ASKPASS"] = os.path.join(HERE, "askpass.sh")
    env["SSH_ASKPASS_REQUIRE"] = "force"
    env["GIT_TERMINAL_PROMPT"] = "0"
    p = subprocess.run(["git"] + args, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                       capture_output=True, text=True, timeout=600)
    if check and p.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {(p.stderr or p.stdout).strip()}")
    return p.stdout


def file_sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest() if os.path.exists(path) else None


def publish(state, repo, branch):
    """Push the top layout, its image and the queued reports; True when nothing is left to push."""
    gds_sha = file_sha(TOP_GDS)
    reports = sorted(f for f in os.listdir(OUTBOX) if f.endswith(".md")) if os.path.isdir(OUTBOX) else []
    if not reports and state.get("published") == [repo, gds_sha]:
        return True
    log(f"publish: {repo} ({len(reports)} report(s)) ...")
    try:
        if (os.path.isdir(os.path.join(PUBLISH_DIR, ".git"))
                and git(["remote", "get-url", "origin"], PUBLISH_DIR, check=False).strip() != repo):
            shutil.rmtree(PUBLISH_DIR)   # --publish-repo changed
        if not os.path.isdir(os.path.join(PUBLISH_DIR, ".git")):
            os.makedirs(os.path.dirname(PUBLISH_DIR), exist_ok=True)
            git(["clone", "-q", "-b", branch, repo, PUBLISH_DIR])
            git(["config", "user.name", os.environ.get("PUBLISH_GIT_NAME", "submit_inverter")], PUBLISH_DIR)
            git(["config", "user.email", os.environ.get("PUBLISH_GIT_EMAIL", "submit_inverter@localhost")],
                PUBLISH_DIR)
        git(["fetch", "-q", "origin", branch], PUBLISH_DIR)
        git(["checkout", "-q", "-B", branch, f"origin/{branch}"], PUBLISH_DIR)
        git(["reset", "-q", "--hard", f"origin/{branch}"], PUBLISH_DIR)
        for sub in ("GDS", "images", "report"):
            os.makedirs(os.path.join(PUBLISH_DIR, sub), exist_ok=True)
        shutil.copy(TOP_GDS, os.path.join(PUBLISH_DIR, "GDS", f"{TOP}.gds"))
        shutil.copy(TOP_CDL, os.path.join(PUBLISH_DIR, "GDS", f"{TOP}.cdl"))
        png = os.path.join(PUBLISH_DIR, "images", f"{TOP}.png")
        if state.get("published") != [repo, gds_sha] or not os.path.exists(png):
            render_image(TOP_GDS, png)
        import dut_map   # status colours change with every processed response: always redraw
        dut_map.draw(state, BASE_GDS, os.path.join(PUBLISH_DIR, "images", "dut_map.png"),
                     os.path.join(PUBLISH_DIR, "images", "dut_map.svg"))
        readme = os.path.join(PUBLISH_DIR, "README.md")
        text = open(readme, encoding="utf-8").read() if os.path.exists(readme) else ""
        if f"images/{TOP}.png" not in text:
            with open(readme, "a", encoding="utf-8") as f:
                f.write(f"\n\n## 最新のレイアウト\n![{TOP}](images/{TOP}.png)\n")
        for r in reports:
            shutil.copy(os.path.join(OUTBOX, r), os.path.join(PUBLISH_DIR, "report", r))
        git(["add", "-A", "GDS", "images", "report", "README.md"], PUBLISH_DIR)
        if git(["status", "--porcelain"], PUBLISH_DIR).strip():
            duts = ", ".join(sorted(state["imported"])) or "なし"
            msg = f"Update {TOP}.gds (取り込み済み DUT: {duts})"
            if reports:
                msg += "\n\nReports: " + ", ".join(reports)
            git(["commit", "-q", "-m", msg], PUBLISH_DIR)
            git(["push", "-q", "origin", f"HEAD:{branch}"], PUBLISH_DIR)
            log(f"publish: pushed {git(['rev-parse', '--short', 'HEAD'], PUBLISH_DIR).strip()}")
        else:
            log("publish: nothing changed")
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as e:
        log(f"publish failed (retried next run): {e}")
        return False
    for r in reports:
        os.remove(os.path.join(OUTBOX, r))
    state["published"] = [repo, gds_sha]
    return True


# ---------------------------------------------------------------- state
def load_state():
    if os.path.exists(STATE_JSON):
        with open(STATE_JSON, encoding="utf-8") as f:
            return json.load(f)
    return {"responses": {}, "retries": {}, "imported": {}}


def save_state(state):
    tmp = STATE_JSON + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1, ensure_ascii=False)
    os.replace(tmp, STATE_JSON)


def reset(stamp):
    """Forget everything processed and put the pristine top back (the current one is backed up)."""
    backup(stamp)
    work = os.path.join(HERE, "run", f"reset_{stamp}")
    build_top([], os.path.join(work, f"{TOP}.gds"))
    shutil.copy(os.path.join(work, f"{TOP}.gds"), TOP_GDS)
    shutil.copy(os.path.join(work, f"{TOP}.cdl"), TOP_CDL)
    for p in (STATE_JSON, os.path.join(HERE, "run", "dut_check_cache.json")):
        if os.path.exists(p):
            os.remove(p)
    for d in (os.path.join(STATE_DIR, "duts"), OUTBOX):
        shutil.rmtree(d, ignore_errors=True)
    log(f"reset: state cleared, {TOP_GDS} / .cdl = pristine top (previous ones in backup/)")


def backup(stamp):
    d = os.path.join(HERE, "backup")
    os.makedirs(d, exist_ok=True)
    for p in (TOP_GDS, TOP_CDL):
        if os.path.exists(p):
            root, ext = os.path.splitext(os.path.basename(p))
            shutil.copy(p, os.path.join(d, f"{root}_{stamp}{ext}"))
    return d


def lock_or_exit():
    """Hold state/lock for the whole run; exit if the previous run is still going."""
    os.makedirs(STATE_DIR, exist_ok=True)
    fd = open(os.path.join(STATE_DIR, "lock"), "w")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("another run is still in progress, exiting")
        sys.exit(0)
    return fd


# ---------------------------------------------------------------- main
def sort_key(s):
    """Form timestamps (e.g. 2026/11/01 12:34:56); falls back to the row order."""
    for fmt in ("%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%m/%d/%Y %H:%M:%S"):
        try:
            return (datetime.datetime.strptime(s["time"], fmt), s["row"])
        except ValueError:
            pass
    return (datetime.datetime.min, s["row"])


def check_submission(s, keys, cache, cache_path, dl_dir, threads):
    """Download / secret key / DUT checks; sets s["status"], s["reason"], s["dut"], s["gds"]."""
    who = f"row {s['row']} ({s['name']})"
    try:
        gds, num, info = download_gds(s, dl_dir)
    except TransientError:
        raise
    except Exception as e:   # noqa: BLE001 - any other download problem is reported
        s["status"], s["reason"] = "download_error", str(e)
        log(f"{who}: download error: {e}")
        return
    s["dut"], s["gds"] = num, gds
    if num not in keys or not hmac.compare_digest(s["key"].strip(), keys[num]):
        s["status"] = "key_mismatch"
        s["reason"] = f"inv_dut_{num:03d} のシークレットキーが一致しません ({info})"
        log(f"{who}: inv_dut_{num:03d}: secret key mismatch")
        return
    digest = hashlib.sha256(open(gds, "rb").read()).hexdigest()
    if digest in cache:
        problems = cache[digest]
        log(f"{who}: inv_dut_{num:03d}: checked before (cache)")
    else:
        log(f"{who}: inv_dut_{num:03d}: DRC / LVS ...")
        problems = check_dut(gds, num, os.path.join(os.path.dirname(gds), "check"), threads)
        cache[digest] = problems
        with open(cache_path, "w") as f:
            json.dump(cache, f, indent=1, ensure_ascii=False)
    if problems:
        s["status"], s["reason"] = "dut_check_error", "; ".join(problems) + f" ({info})"
        log(f"{who}: inv_dut_{num:03d}: " + "; ".join(problems))
    else:
        s["status"], s["reason"] = "checked", info
        log(f"{who}: inv_dut_{num:03d}: OK")


def integrate(chosen, existing, work, threads, top_check):
    """Build base + existing + chosen DUTs and check it; drop the chosen DUTs that break it.

    chosen: {dut: submission} (new), existing: {dut: gds} (imported before).
    Returns (top_gds, imported dut list, notes)."""
    top_gds = os.path.join(work, "top", f"{TOP}.gds")
    cands = sorted(chosen)

    def files(nums):
        f = dict(existing)
        f.update({n: chosen[n]["gds"] for n in nums})
        return [f[n] for n in sorted(f)]

    if not top_check:
        build_top(files(cands), top_gds)
        return top_gds, cands, ["--no-top-check: 統合後の DRC / LVS は実行していません"]

    sub_dir = os.path.join(work, "bisect")
    sub_gds = os.path.join(sub_dir, f"{TOP}.gds")

    def drc_test(nums):
        build_top(files(nums), sub_gds)
        return check_top(sub_gds, threads, lvs=False)[0]

    def lvs_test(nums):
        build_top(files(nums), sub_gds)
        return check_top(sub_gds, threads, drc=False)[1]

    for attempt in range(1, 6):
        log(f"top: {len(existing)} imported + {len(cands)} new DUT(s), DRC / LVS (attempt {attempt}) ...")
        build_top(files(cands), top_gds)
        drc_ok, lvs_ok, out = check_top(top_gds, threads)
        if drc_ok and lvs_ok:
            return top_gds, cands, [f"統合後の DRC (密度以外) / LVS: OK ({work}/top)"]
        if not cands:
            raise TransientError(f"the top without new DUTs fails the check ({work}/top):\n{out}")
        bad = {}
        if not drc_ok:
            lyrdb = os.path.join(work, "top", "drc", f"{TOP}.lyrdb")
            if not os.path.exists(lyrdb):
                raise TransientError(f"top DRC did not run (see {work}/top/drc/drc.log)")
            blamed, other = drc_culprits(lyrdb, cands)
            for n, rules in blamed.items():
                bad[n] = "統合後の DRC エラー: " + ", ".join(sorted(rules))
            if other and not blamed:
                log("top: DRC errors outside the DUT frames, bisecting the new DUTs ...")
                for n in bisect_bad(cands, drc_test):
                    bad[n] = "統合後の DRC エラー: " + ", ".join(sorted(other))
        if not lvs_ok and not bad:
            if "LVS FAILED TO RUN" in out:
                raise TransientError(f"top LVS did not run (see {work}/top/lvs/lvs.log)")
            log("top: LVS mismatch, bisecting the new DUTs ...")
            for n in bisect_bad(cands, lvs_test):
                bad[n] = "統合後の LVS 不一致"
        for n, reason in bad.items():
            chosen[n]["status"], chosen[n]["reason"] = "top_check_error", reason
            log(f"top: inv_dut_{n:03d} dropped: {reason}")
        cands = [n for n in cands if n not in bad]
    raise TransientError(f"top still fails after 5 attempts (see {work}/top)")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--responses", help="form responses CSV")
    src.add_argument("--sheet-id", help="responses spreadsheet ID (Sheets API)")
    ap.add_argument("--range", default="A1:Z", help="sheet range (default: first sheet A1:Z)")
    ap.add_argument("--credentials", help="service account key JSON")
    ap.add_argument("--threads", type=int, default=os.cpu_count() or 1)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-top-check", action="store_true")
    ap.add_argument("--publish-repo", help="git repository to push the results to (SSH URL)")
    ap.add_argument("--publish-branch", default="main")
    ap.add_argument("--reset", action="store_true",
                    help="forget what has been processed and restore the pristine top first")
    args = ap.parse_args()
    if not (args.responses or args.sheet_id or args.reset):
        ap.error("one of --responses / --sheet-id (or --reset) is required")

    lock = lock_or_exit()   # noqa: F841 - held until exit
    sys.path.insert(0, WORKSHOP_LAYOUT)
    if not os.path.exists(BASE_GDS):
        os.makedirs(BASE_DIR, exist_ok=True)
        shutil.copy(TOP_GDS, BASE_GDS)
        log(f"pristine top saved as {BASE_GDS}")

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    while os.path.exists(os.path.join(HERE, "run", stamp)):   # two runs within a second
        stamp += "_"
    if args.reset:
        reset(stamp)
        if not (args.responses or args.sheet_id):
            return

    state = load_state()
    subs = parse_responses(read_response_rows(args))
    for s in subs:
        s["id"] = response_id(s)
    new = sorted((s for s in subs if s["id"] not in state["responses"]), key=sort_key)
    seen = set()
    new = [s for s in new if not (s["id"] in seen or seen.add(s["id"]))]   # identical duplicates
    log(f"{len(subs)} response(s), {len(subs) - len(new)} processed before, {len(new)} new")
    if not new:
        if args.publish_repo and not args.dry_run and publish(state, args.publish_repo, args.publish_branch):
            save_state(state)
        return

    work = os.path.join(HERE, "run", stamp)
    os.makedirs(work, exist_ok=True)
    keys = read_keys(KEY_CSV)
    cache_path = os.path.join(HERE, "run", "dut_check_cache.json")
    cache = json.load(open(cache_path)) if os.path.exists(cache_path) else {}

    # ---- per new response: download, secret key, DUT DRC / LVS
    for s in new:
        try:
            check_submission(s, keys, cache, cache_path, os.path.join(work, "download"), args.threads)
        except TransientError as e:
            tries = state["retries"].get(s["id"], 0) + 1
            if not args.dry_run:
                state["retries"][s["id"]] = tries
            if tries < MAX_TRIES:
                s["status"], s["reason"] = "retry", f"{e} ({tries}/{MAX_TRIES} 回目)"
            else:
                s["status"] = "download_error" if s["dut"] is None else "dut_check_error"
                s["reason"] = f"{MAX_TRIES} 回とも失敗: {e}"
            log(f"row {s['row']} ({s['name']}): temporary error: {e}")

    # ---- newest passing new response per DUT
    chosen = {}
    for s in new:
        if s["status"] == "checked":
            if s["dut"] in chosen:
                chosen[s["dut"]]["status"] = "superseded"
                chosen[s["dut"]]["reason"] = f"row {s['row']} の投稿を使用"
            chosen[s["dut"]] = s
    existing = {int(n): os.path.join(HERE, d["gds"]) for n, d in state["imported"].items()}
    notes = [f"新しいフォーム回答 {len(new)} 件、チェック合格の DUT {len(chosen)} 個、"
             f"取り込み済みの DUT {len(existing)} 個"]

    if args.dry_run:
        notes.append("--dry-run: トップレイアウトも処理状況も書いていません")
    elif chosen:
        try:
            top_gds, imported, top_notes = integrate(chosen, existing, work, args.threads,
                                                     not args.no_top_check)
        except TransientError as e:
            # the new DUTs stay unprocessed: next run retries them
            for s in chosen.values():
                s["status"], s["reason"] = "retry", f"トップの統合に失敗: {e}"
            imported, top_notes = [], [f"トップの統合に失敗 (次回再実行): {e}"]
            log(f"top: {e}")
        notes += top_notes
        if imported:
            backup_dir = backup(stamp)
            shutil.copy(top_gds, TOP_GDS)
            shutil.copy(os.path.splitext(top_gds)[0] + ".cdl", TOP_CDL)
            os.makedirs(os.path.join(STATE_DIR, "duts"), exist_ok=True)
            for n in imported:
                s = chosen[n]
                rel = os.path.join("state", "duts", f"inv_dut_{n:03d}.gds")
                shutil.copy(s["gds"], os.path.join(HERE, rel))
                old = state["imported"].get(f"{n:03d}")
                if old and old["id"] in state["responses"]:
                    state["responses"][old["id"]]["status"] = "superseded"
                    state["responses"][old["id"]]["reason"] = f"{s['time']} の投稿で置き換え"
                state["imported"][f"{n:03d}"] = {"id": s["id"], "time": s["time"], "gds": rel}
                s["status"] = "imported"
            notes.append(f"今回取り込んだ DUT ({len(imported)} 個): " + ", ".join(f"{n:03d}" for n in imported))
            notes.append(f"{TOP_GDS} / .cdl を更新 (以前のものは {backup_dir}/)")

    if not args.dry_run:
        now = datetime.datetime.now().isoformat(timespec="seconds")
        for s in new:
            if s["status"] != "retry":
                state["responses"][s["id"]] = {k: s[k] for k in ("row", "time", "name", "mail", "url",
                                                                 "dut", "status", "reason")}
                state["responses"][s["id"]]["processed_at"] = now
                state["retries"].pop(s["id"], None)
                write_public_report(s, stamp, OUTBOX)
        save_state(state)
        os.makedirs(os.path.join(HERE, "reports"), exist_ok=True)
        write_status(state, os.path.join(HERE, "reports", "status.csv"))
        notes.append(f"取り込み済みの DUT 合計 {len(state['imported'])} 個: "
                     + (", ".join(sorted(state["imported"])) or "なし"))

        if args.publish_repo:
            ok = publish(state, args.publish_repo, args.publish_branch)
            save_state(state)
            notes.append(f"{args.publish_repo} に push しました" if ok
                         else "push に失敗しました (次回再実行)")

    md, _ = write_report(new, os.path.join(HERE, "reports"), stamp, notes)
    for n in notes:
        log(n)
    rejected = sum(s["status"] in REJECTED for s in new)
    log(f"report: {md} ({rejected} problem submission(s))")


if __name__ == "__main__":
    main()
