# submit_inverter

Google フォームで投稿された DUT (`inv_dut_NNN.gds`) をチェックして `workshop_inverter.gds` に取り込む。

| ファイル | 内容 |
|---|---|
| `import_submissions.py` | 取り込みスクリプト (処理の詳細はファイル先頭の docstring) |
| `run_batch.sh` | cron から 30 分ごとに呼ぶバッチ (設定は `batch.env`、ひな形は `batch.env.example`) |
| `dut_map.py` | 処理状況で色分けした DUT マップを描く (`python3 dut_map.py` で手元にも出せる) |
| `askpass.sh` | push 時に SSH 鍵のパスフレーズ (`batch.env` の `SSH_KEY_PASSPHRASE`) を ssh に渡す |
| `state/outbox/` | まだ push していない公開用レポート (push に成功すると消える) |
| `publish/repo/` | 公開先リポジトリの作業コピー (毎回リモートに合わせてから更新) |
| `state/state.json` | 処理状況 (処理済みのフォーム回答とその結果、取り込み済みの DUT) |
| `state/duts/` | 取り込み済み DUT の GDS |
| `reports/status.csv` | これまでに処理した全回答の状態 (毎回更新) |
| `logs/batch_YYYYMMDD.log` | バッチの実行ログ |
| `dut_params_secretkey.csv` | DUT パラメータと DUT ごとのシークレットキー (`secret_key` 列) |
| `workshop_inverter.gds` / `.cdl` | 取り込み結果のトップレイアウトと LVS 用ネットリスト |
| `base/workshop_inverter_base.gds` | 取り込み前のトップ (初回実行時に保存。毎回これ＋取り込み済み DUT から作り直す) |
| `reports/report_<時刻>.md` / `.csv` | その回に処理した回答のレポート (問題のある投稿は お名前・連絡先メールアドレス付き) |
| `run/<時刻>/` | ダウンロードした GDS、DUT ごとの DRC / LVS、トップの DRC / LVS のログ |
| `backup/` | 上書き前の `workshop_inverter.gds` / `.cdl` |

## 処理の流れ

1. フォームの回答 (お名前 / 連絡先メールアドレス / シークレットキー / GitHub の RUN URL) を読む
2. GitHub API で RUN のコミットを調べ、そのコミットの `src/inv_dut_NNN.gds` をダウンロード
3. シークレットキーを `dut_params_secretkey.csv` の DUT_NUM = NNN の `secret_key` と照合
4. `~/template_inverter/RISC-V-Day_MPW_template` の CI と同じ Pre-check / DRC (`all,-density,inv_dut`) / LVS
   (デッキは `~/template_inverter/gf180mcuD`)、および `replace_dut.py` のフレームチェック
5. 合格した DUT を `~/workshop_inverter/layout/replace_dut.py` でトップに入れ、
   `check_top.py` で `~/pdk/gf180mcuD` の DRC (密度以外) と LVS を実行。
   エラーになった DUT は特定して外し、作り直して再チェック。
   LVS は `check_ports=false` で実行する (2026/10/20 更新の PDK から既定で有効になったトップのポートチェックは、
   フラット化した tgate / DUT 内のピンラベルで必ず失敗するため。回路の照合は従来どおり行う)
6. クリーンになったものを `workshop_inverter.gds` / `.cdl` に書き出し、レポートを作成
   (`reports/`: お名前・連絡先メールアドレス付き。成功した投稿も含む)
7. 公開先リポジトリ (`--publish-repo`) に push (下記「公開リポジトリへのアップロード」)

キー不一致・ダウンロード失敗・DRC/LVS エラーの投稿は取り込まない。
同じ DUT 番号の投稿が複数ある場合は、合格したもののうち一番新しいものを使う (取り込み済みの DUT も新しい投稿で置き換わる)。

## バッチ運用 (処理済みの管理)

- 処理済みの回答は `state/state.json` に回答の内容のハッシュで記録され、次回からは新しい回答だけを処理する。
  新しい回答がなければ何もせずに終わる (レポートも作らない)
- トップは `base/workshop_inverter_base.gds` ＋ 取り込み済み DUT (`state/duts/`) ＋ 今回合格した DUT で作り直し、
  統合後のエラーは今回の DUT だけから原因を探す
- GitHub のレート制限・ネットワークエラー・DRC/LVS が動かなかった、などの一時的なエラーは処理済みにせず次回やり直す
  (3 回失敗したらエラーとしてレポート)
- 前の回がまだ実行中 (`state/lock`) なら、その回は何もせずに終わる (トップの DRC/LVS は 10 分以上かかる)
- 最初からやり直すには `--reset` (リセットオプション)。処理状況 (`state/state.json`、`state/duts/`、`state/outbox/`) と
  DUT チェックのキャッシュを消し、`workshop_inverter.gds` / `.cdl` を `base/workshop_inverter_base.gds` から作り直す
  (上書き前のものは `backup/` に残る)。次の実行で全ての回答をもう一度処理する
  - `python3 import_submissions.py --reset` … リセットだけ
  - `./run_batch.sh --reset` … リセットしてそのまま全回答を処理 (`batch.env` の設定で push まで)
  - 公開リポジトリの `report/` にある過去のレポートは消さない (GDS と画像は次の push で上書きされる)

```bash
cp batch.env.example batch.env && chmod 600 batch.env   # SHEET_ID などを書く
crontab -e
# */30 * * * * /home/noritsuna/submit_inverter/run_batch.sh
```

## 公開リポジトリへのアップロード

`--publish-repo` (`batch.env` の `PUBLISH_REPO`) を指定すると、処理のあとで
<https://github.com/ishi-kai/RISC-V-Day_2611_inverter_workshop> に次のものを commit / push する。

| 公開先 | 内容 |
|---|---|
| `GDS/workshop_inverter.gds` / `.cdl` | 最新のトップレイアウトと LVS 用ネットリスト |
| `images/workshop_inverter.png` | 最新の `workshop_inverter.gds` のレイアウト画像 (色はこのディレクトリの `gf180mcu.lyp`、`INV_IMAGE_LYP` で変更可。無ければ KLayout 標準の色。ラベル文字は非表示)。`README.md` で表示している (無ければ末尾に追記) |
| `images/dut_map.png` / `.svg` | DUT マップ (`dut_map.py`、配置は `base/workshop_inverter_base.gds` から)。各 DUT を処理状況で色分けする: **緑** = 取り込み済み、**黄** = 取り込み済みだがその後の再投稿がエラー (取り込み済みのものを使用)、**赤** = 投稿があったが取り込めていない、**薄青** = 未投稿。push のたびに描き直す |
| `report/NNN_<YYYYMMDD_HHMMSS>.md` | 今回処理した回答ごとのレポート (NNN は inv_dut_NNN の番号、RUN URL が読めず番号が分からない場合は `unknown`)。結果、チェックごとの OK/エラー、エラーの詳細、GitHub の RUN URL を載せ、**お名前・連絡先メールアドレス・シークレットキーは載せない** |

- push の前に作業コピー (`publish/repo/`) をリモートの最新に合わせるので、他の人の commit とぶつからない
- push に失敗した場合 (ネットワーク、認証など) はレポートを `state/outbox/` に残し、次の実行で (新しい回答が無くても) やり直す
- 認証は SSH 鍵 `SSH_KEY` (既定 `~/.ssh/id_ed25519`) で、パスフレーズは `SSH_KEY_PASSPHRASE` から `askpass.sh` 経由で渡す。
  鍵の公開鍵 (`~/.ssh/id_ed25519.pub`) を GitHub アカウントか公開先リポジトリの Deploy key (Allow write access) に登録しておくこと
- commit の作者は `PUBLISH_GIT_NAME` / `PUBLISH_GIT_EMAIL`
- `batch.env` にはパスフレーズが入るので `chmod 600` にし、Git などに入れないこと

## 実行

```bash
# 回答スプレッドシートを CSV でダウンロードした場合
python3 import_submissions.py --responses responses.csv

# Sheets API で直接読む場合 (下記「アクセスの準備」)
python3 import_submissions.py --sheet-id <スプレッドシートID> --credentials ~/.config/inv-form-sa.json

# DUT のチェックだけ (トップも処理状況も書かない、push もしない)
python3 import_submissions.py --responses responses.csv --dry-run

# 結果を GitHub に push する (SSH_KEY_PASSPHRASE を環境変数で渡す)
python3 import_submissions.py --sheet-id <ID> --credentials <鍵 JSON> \
    --publish-repo git@github.com:ishi-kai/RISC-V-Day_2611_inverter_workshop.git

# リセット (処理済みの記録を消してトップを元に戻す)
python3 import_submissions.py --reset
```

所要時間の目安: DUT 1 個あたり DRC/LVS で約 1 分 (同じ GDS は `run/dut_check_cache.json` で再チェックを省略)、
トップの DRC/LVS で約 10 分。

## アクセスの準備

### フォームの回答

フォームの回答はフォームのオーナー (と共同編集者) しか見られない。以下のどちらかで読む。

**A. CSV でダウンロード (簡単)**

1. フォームの編集画面 → 「回答」タブ → 「スプレッドシートにリンク」で回答シートを作る
2. スプレッドシートで「ファイル」→「ダウンロード」→「カンマ区切り形式 (.csv)」
3. `--responses <ダウンロードした CSV>` で実行

**B. Sheets API (サービスアカウント、繰り返し実行する場合)**

1. <https://console.cloud.google.com/> でプロジェクトを作る (既存でも可)
2. 「API とサービス」→「ライブラリ」で **Google Sheets API** を有効にする
3. 「IAM と管理」→「サービスアカウント」→ 作成 (ロールは不要)
4. 作ったサービスアカウント →「鍵」→「鍵を追加」→「JSON」で鍵をダウンロード
   (例: `~/.config/inv-form-sa.json`、`chmod 600` にして Git などに入れないこと)
5. 回答スプレッドシートの「共有」で、サービスアカウントのメールアドレス
   (`xxx@<project>.iam.gserviceaccount.com`) を **閲覧者** として追加
6. スプレッドシート ID は URL の `https://docs.google.com/spreadsheets/d/<ここ>/edit`
7. `pip install google-auth requests`
8. `--sheet-id <ID> --credentials <鍵 JSON>` で実行 (`$GOOGLE_APPLICATION_CREDENTIALS` でも可)

回答が最初のシート以外にある場合は `--range "'フォームの回答 1'!A1:Z"` のようにシートを指定する。

注意: スプレッドシートを「ウェブに公開」すれば認証なしで読めるが、メールアドレスとシークレットキーが
公開されてしまうので使わないこと。

### GitHub

公開リポジトリならトークンなしで動く (API は 1 時間に 60 回まで、1 投稿あたり 2 回使う)。
投稿が多いときやリポジトリが非公開のときは、トークンを作って `GITHUB_TOKEN` に設定する。

1. GitHub →「Settings」→「Developer settings」→「Personal access tokens」→「Fine-grained tokens」→「Generate new token」
2. Repository access は「Public repositories (read-only)」で良い (非公開リポジトリを読む場合はそのリポジトリの Contents: Read-only)
3. `export GITHUB_TOKEN=github_pat_...` してから実行 (1 時間に 5000 回まで)

## 必要なもの

- KLayout 0.30.9 以降と Python の `klayout`, `pyyaml`, `click` パッケージ
- `~/template_inverter/RISC-V-Day_MPW_template` と `~/template_inverter/gf180mcuD` (DUT チェック)
- `~/workshop_inverter` と `~/pdk/gf180mcuD` (トップの差し替えと DRC/LVS)

パスは環境変数 `INV_TEMPLATE`, `INV_TEMPLATE_PDK`, `INV_WORKSHOP_LAYOUT`, `GF180_PDK` で変えられる。
