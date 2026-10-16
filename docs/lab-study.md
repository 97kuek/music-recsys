# music-recsysのMusic4All被験者実験準備

確認日: 2026-10-16。Music4Allを優先、外部音楽サービスとCloudflareは選択肢として保持
現在は開発用検証。実被験者データ収集・実音源配信は開始しない
研究室サーバーの外部公開・ポート開放は禁止。Cloudflare Tunnel等による公開も行わない

## 現状と開始条件

| 対象 | 現状 | 開始までに必要なもの |
| --- | --- | --- |
| 音源 | リポジトリdata以下に実曲なし、独自合成WAV12本のみ | Music4All clip保存先・提供条件・制限付きWeb試聴の許諾 |
| 楽曲表示 | 学習カタログ45,888曲、タイトル/artist接続0曲 | 原版ID対応メタデータ |
| Popularity | 総再生回数/再生ユーザー数の実モデル2条件 | 試聴可能な共通候補への対応 |
| ItemCF | Cosine/Jaccard×20/50/100の実モデル6条件 | 比較条件を明示、採用モデルは未確定 |
| ALS/BPR | 実データ重み未発見、デモ用の小規模重みだけ | Validation比較設定を明示して学習＋export |
| 研究同意 | 開発用24時間保持・任意保存・削除のみ | 説明文/保持期間/撤回/参加者アクセス方針の決定 |
| ホスト | 現在sushi、tyan/microの利用権限・接続先は未確認 | 管理者の利用許可・経路・保存領域の確認 |

今回の音源監査範囲は `data/`。既存学習IDとファイル名stemの対応は0/45,888
これは手元の確認範囲に対する値であり、Music4All全体の配布音源の存在率ではない
別サーバーや未マウント領域の音源は未確認。共有領域を無断探索しない
形式・実容量・音声長は未取得なので未測定。合成音源の容量をMusic4All音源容量としない

## 音源と許諾

[Music4All公式](https://sites.google.com/view/contact4music4all)は109,269曲・30秒clip・ID/曲名/artist/spotifyidと提供申請先を説明
取得申請先: **contact4music4all@gmail.com**（公式サイト確認日: 2026-10-16）
必要なのは30秒clip、元の16文字IDと曲名/artistの対応表、取得・利用条件の3点
公開Onion履歴を取得しただけではclipと曲名対応表は揃わない
[Onion v2](https://zenodo.org/records/15394646)は履歴・特徴量の公開であり、原曲をWeb配信する包括許諾とは扱わない
提供元への問い合わせ事項（送信は未実施）:
- 研究室所属前の教育プロジェクト、少人数の仮名ユーザーへの30秒clip試聴
- アクセス制限付き研究室サーバーでの複製・配信、学内/学外の参加範囲
- タイトル・artist・画像等の表示、帰属表示、取得経路、期限、音源削除条件
- 行動ログ・主観評価の収集/分析、将来の研究発表・一般公開に別許諾が必要か
- 研究室が取得済みの場合、当初の提供条件が上記用途を含むか

音源の所在が判明したら読取り監査を再実行する。許諾のない音源は配信しない

```bash
.venv/bin/python -m music_recsys.webapp.audit \
  --config data/app/music4all/config.json --audio-root /許可された音源保存先 \
  --output data/app/music4all/audio-audit-new.json
```

監査は指定ルート、形式、容量、ID対応数、モデルhash/ロード/推論を保存。設定選択・再学習なし
現在の結果: `data/app/music4all/audit-20261016.json`

許諾後の登録は `music_recsys.webapp.media`。新規bundleだけを作り、既存結果は上書きしない
メタデータCSVは `item_id,title,artists`、複数artistは `|` 区切り
音源はファイルstemがitem_idと一致すること。曖昧一致・同一IDの複数音源・ルート外symlinkを拒否
別版の音声を同じIDへ手動流用しない。ファイル名一致だけで権利・録音同一性は証明できない
許諾JSONは人が確認した証拠への参照を記録する。任意の文字列で法的許諾が発生するわけではない

```json
{
  "web_preview": true,
  "access_scope": "restricted-study",
  "evidence_ref": "確認済み提供条件の内部参照",
  "expires_at": "許諾期限のtimezone付きISO日時",
  "item_ids": ["明示的に許諾された楽曲ID"]
}
```

```bash
.venv/bin/python -m music_recsys.webapp.media \
  --config data/app/music4all/config.json --metadata /private/tracks.csv \
  --audio-root /private/clips --permission /private/permission.json \
  --output data/app/music4all-listening
```

音源を新bundleへコピーしhash検証、許諾記録のhashを保持。コピー分の空き容量が必要
モデルは元の絶対パス参照を保持するので、別ホストへ移す際はモデル配置と参照を再確認する
WAV/MP3/FLAC/OGG/M4Aを登録可能、ブラウザcodec対応は実音源入手後に検証する
音声APIは認可済み推薦と再生IDを照合し、形式に応じたContent-Typeで返す
デモ音源を実曲IDへ貼り付けて実曲試聴済みとしない

## モデルと測定

実データ8モデルで3回warmup＋20要求、3曲seed・K=20、CPU/1スレッドの配信計算を実測

| 条件 | 中央値ms | 新規ユーザー |
| --- | ---: | --- |
| Popularity再生回数 / 再生ユーザー数 | 6.35 / 6.64 | 保存済み統計 |
| ItemCF Cosine20 / 50 / 100 | 7.10 / 6.77 / 6.31 | 選択曲の近傍 |
| ItemCF Jaccard20 / 50 / 100 | 6.25 / 6.25 / 6.27 | 選択曲の近傍 |

8モデル常駐Runtimeロード約0.91秒、最大RSS約364 MiB。FastAPI負荷試験・ネット往復は含まない
逐次ロード監査のRSS約168 MiBと全モデル常駐値を混同しない
sushiで62 GiB RAM、当該filesystem空き約20 GiB、RTX3090 24 GiBは確認時0%/1 MiB使用
tyan/microの構成・空き容量は未確認。このGPU情報を他ホストへ外挿しない
大規模学習前は再度ホストnvidia-smiを確認し、共有利用規則に従ってGPUを優先

ALSは保存済み楽曲因子のfold-in実装あり、今回実データ因子がなく未実測
BPR未知ユーザーはPopularity補完。これをBPR固有の性能として比較してはいけない
現行8モデルはValidationの学習入力から作成済み、17条件の評価結果は比較用で採用設定は未確定
ALS/BPR再学習は設定/計算計画を明示して別出力へexport。評価JSONを重みとしてロードしない
[モデル配信](model-serving.md)の保存・fold-in・候補制約を参照

## 操作とログ

曲を試聴して初期選択→推薦リスト→任意の満足度1問。条件秘匿を維持
通常モードは目的を選択。比較モードは現在discover固定・同じ初期選択・候補/K・各条件1要求
同一被験者に複数条件を提示し、seedと仮名IDで順序を記録。少人数での均等割付は未実装
モデル名/設定/実際のfallbackはサーバーログへ保存し被験者へ表示しない

追加ログ:
- セッションの初期プロフィール、各推薦時のプロフィールと実聴取曲を別保存
- 順位はresponse.item_idsの順序、条件・モデル版・offline_run_ref・目的・推論時間は既存契約
- listened_secondsは再生ID単位の累積実再生進行時間のクライアント計測値
- シークで進んだ位置を試聴時間に加算しない。position_secondsとは別、同じ再生IDは最大累積値を用いる
- response_latency_msはAPI呼出しから応答まで、display_latency_msは同じ開始からカード初回可視まで
- 後でスクロールしたカードのdisplay時間は操作待ちを含む。初回カード表示遅延と分けて分析する
- ブラウザ終了や通信切断前の未送信分は欠測。本人が本当に聴いた証明にはならない
- 初期選択は再生履歴・Repeat Recallの正解ではない

同意なしはメモリのみ、同意ありでも現状は開発用24時間SQLite。実験用保持期間を勝手に決めない
本人セッションから削除可能、再起動後の本人認証/撤回窓口とバックアップ削除は本番前に決定する
オフライン指標をオンライン主観評価へ混ぜず、モデル版・条件参照で対応付ける
LLM/AgentはStrategy交換で追加、今回実装しない

## 研究室への配置

React buildをFastAPIから同一Origin配信し、loopbackだけで起動する。Docker・常駐DB不要
SQLiteは研究室の許可された永続領域、モデル/音源は非公開領域へ置く
1 workerで起動、現行メモリ内セッションを複数workerへ無条件分散しない

```bash
uv sync --locked --group app --group data --group models
cd frontend
npm ci
npm run build
cd ..
# 明示的な合成音源の開発用比較
.venv/bin/python -m music_recsys.webapp.server \
  --config data/app/demo/config.json --study deploy/demo-study.json \
  --database data/app/development.sqlite --host 127.0.0.1 --port 8765
```

デモ未準備時は[アプリMVP](app-mvp.md)の準備手順を使う
サーバー内の接続先は `http://127.0.0.1:8765`。外部インターフェースへbindしない
SSHポート転送は利用可否未確認。通常のSSHログイン権限から転送の許可を推定しない
実験用のSSHアカウント共有はしない。学外接続は研究室VPN/SSH等の許可経路を管理者へ確認する
常駐化テンプレートは `deploy/music-recsys.service.example`。user systemd利用許可後にコピー・パスを調整する
`~/.config/otolab/service.env`に絶対パスのOTOLAB_CONFIG/OTOLAB_DATABASE/OTOLAB_STUDYを設定し0600で保持
systemdのenable/start・linger設定・ポート開放・外部公開は今回実施していない
Cloudflareフロントエンドから研究室APIへ接続しない。既存Cloudflare設定は別環境用の選択肢として保持
被験者向け運用は[個人Macへ移行](mac-study.md)。自宅回線での遠隔接続は研究室環境と分離する
既存Bearerはバックエンド保護用で参加者認証の代わりではない

管理者確認事項: tyan/micro使用可否、SSH/VPN、CPU/RAM/GPU制限、永続領域/quota/backup、user service、ローカル利用/SSH転送の可否
実験責任者確認事項: 音源許諾、保持/削除期間、比較条件、fallback扱い、説明同意、3〜5人の予備実験実施可否

## 検証と残作業

単体/統合テスト: `tests/test_lab_study.py`、既存webapp/study/foundation/model_artifactsと合わせて確認
合成音源で2条件秘匿→実再生進行→停止/時間記録→任意評価→終了→削除をブラウザ確認
実曲E2Eは音源・メタデータ・許諾が未取得なので未実行
順序は音源と提供条件確認→metadata/audio登録→ALS/BPR重み準備→条件固定→ログ/同意/アクセス方針確定→予備実験
