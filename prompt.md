# 投稿されたDUT(インバータ回路)を取り込むスクリプトの作成
~/template_inverter/RISC-V-Day_MPW_template を利用して作成されたgithubから、src/inv_dut_NNN.gdsを取り込んで、~/workshop_inverter/のスクリプトを利用して、「workshop_inverter.gds」に取り込んでください。

## 条件
- https://docs.google.com/forms/d/e/1FAIpQLSc03BBdM_YsMIQLg8rjOAOwWR8Er7i9uSUOLvgvSsFZ5M1xpg/viewform?usp=publish-editor から必要な情報を取り込む
    - アクセス権がない可能性があるので、アクセスに必要な情報などがあれば作成法など含めて提示してください。
- 「githubのURLとRUN ID(例：https://github.com/noritsuna/RISC-V-Day_MPW_template/actions/runs/39216792435)」から、そのRUN IDに該当する「src/inv_dut_NNN.gds」をダウンロードしてください。
- 「シークレットキー」とダウンロードした「inv_dut_NNN.gds」のNNNがdut_params_secretkey.csvのDUT_NUM列なので、secret_key列がマッチしているか確認する。マッチしない場合は、「お名前」と「連絡先メールアドレス」を含めて、レポートを作成してください。
- ~/template_inverter/と同じDRCとLVSを実施して、問題ないことを確認してください。問題がある場合は、「お名前」と「連絡先メールアドレス」を含めて、レポートを作成してください。
- 統合したworkshop_inverter.gdsに、~/pdk/gf180mcuD/のDRCとLVSを実行して、密度エラー以外は問題がないことを確認してください。問題がある場合は、「お名前」と「連絡先メールアドレス」を含めて、レポートを作成してください。
- 「シークレットキー」がミスマッチである場合やDRCやLVSエラーの場合は、取り込まないでください。問題がある場合は、「お名前」と「連絡先メールアドレス」を含めて、レポートを作成してください。
- 成功した場合も、「お名前」と「連絡先メールアドレス」を含めて、レポートを作成してください。
- 30分ごとに実行するバッチファイルとして利用する予定なので、どこまでが処理済みかを保存しておいてください。
    - リセットオプションも用意しておいてください。
- README.mdにドキュメントを作ってください。

### 「workshop_inverter.gds」のアップロード
- https://github.com/ishi-kai/RISC-V-Day_2611_inverter_workshop.git のGDSディレクトリに「workshop_inverter.gds」をアップロードしてください。
- imagesディレクトリに、最新の「workshop_inverter.gds」のレイアウト画像を保存して、README.md内で表示するようにしてください。
- reportディレクトリに作成したレポートを「inv_dut_NNN」の「NNN_[作成した日時].md」としてアップロードしてください。。アップロードする内容に「お名前」と「連絡先メールアドレス」と「シークレットキー」は含めないでください。「githubのURLとRUN ID(例：https://github.com/noritsuna/RISC-V-Day_MPW_template/actions/runs/39216792435)」は含めてもOKです。

### 「dut_map」のアップロード
- https://github.com/ishi-kai/RISC-V-Day_2611_inverter_workshop/blob/main/images/dut_map.png や https://github.com/ishi-kai/RISC-V-Day_2611_inverter_workshop/blob/main/images/dut_map.svg の各DUTの色を成功したら緑に失敗したら赤、すでに成功した投稿がされていてアップデートしたが失敗した場合は黄色にしてください。
- https://github.com/ishi-kai/RISC-V-Day_2611_inverter_workshop/README.md に上記の説明を入れてください。
