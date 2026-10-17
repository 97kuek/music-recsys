# UIを含むアプリ全体のアーキテクチャ

## 配置とデータフロー

```mermaid
flowchart LR
    subgraph Lab[研究室: 外部公開しない]
      D[学習用Parquet / 分割・特徴] --> B[バッチ学習・オフライン評価]
      B --> M[manifest付き配信モデル]
    end
    M -->|許可された成果物を転送| F[個人Macのbundle / 読取り専用]
    subgraph Mac[個人Mac]
      A[元データ保管 / 必要曲の情報・MP3] --> F
      subgraph Container[Docker 1コンテナ]
        F --> L[FastAPI / 起動時load / 推論・音声API]
        U[React build / 同じFastAPIから配信] --> L
      end
      L --> S[stateのSQLite / 評価・操作ログ]
      T[ホストのcloudflared] --> L
    end
    P[被験者ブラウザ] --> C[Cloudflare Access認証]
    C -->|HTTPS / Tunnel| T
```

- React＋TypeScript＋Vite、FastAPI、既存RecommendationServiceのモジュラーモノリス
- Pages・Workersは使用しない、cloudflaredはMacのホストで動かし、Dockerはアプリ1コンテナだけ
- 学習/評価はバッチ、アプリは保存済みモデルを起動時に検証ロードしてリアルタイム推論する
- APIリクエストごとの学習・重み再読込み・モデル選択の自動確定は行わない
- 研究室サーバーの外部公開・ポート開放・Tunnel起動は禁止、Mac側の遠隔設定と分離する

## 保存先・コンポーネント

| 要素 | 実体・保存場所 |
| --- | --- |
| 学習データ/評価結果 | 研究室のParquet/JSON/PNG、[コード実行フロー](experiment-workflow.md#保存物) |
| Music4All元データ | Mac内蔵SSDの非公開source領域、取得record・hashを併置、外付けSSDは使用しない |
| 配信モデル | `data/models/`または`data/app/`のmanifest＋NPY、ユーザー因子はサーバー専用 |
| 配信用bundle | config・catalog・モデル・音源・transfer.json、Macで`/bundle`へread-only mount |
| 楽曲情報 | TrackCatalog/CatalogTrack/TrackRef、catalog.json等のhash固定ファイル、常駐DBは不要 |
| 音源 | 実験の固定楽曲集合に必要な許諾済みMP3等だけをbundleへ配置、source領域をHTTP公開しない |
| React | `frontend/src`、通常buildは`src/interfaces/http/static`へ生成 |
| ログ | Macの`~/music-recsys/state/sessions.sqlite`、Dockerでは`/state/sessions.sqlite` |
| 管理export | 同stateの`exports/SESSION_ID.json`、[権限・期限・削除](online-study.md#保存権限削除) |

- 名称・Compose project・コンテナ利用者・主要環境変数をmusic-recsysへ統一する
- 設定は`MUSIC_RECSYS_*`、旧`OTOLAB_*`は読込み互換だけ、新名を優先する
- Dockerのstateは更新するコード/ZIPフォルダの外、通常の掃除でログを消さない
- 少人数向け1 workerと直列化を使い、一般公開の同時接続性能は未検証
- 8実モデル常駐の既存測定は約364MiB・ロード0.91秒、Macでの値は別途測定する
- GPUバッチ評価と単発配信のNumPy CPU経路は別、デバイス選択は実測に基づく

## 容量と元データの配置

- 研究室には学習・評価で必要な加工物を保持し、元データからの変換も研究室のバッチ準備として実行する
- `lab_data`の取得キャッシュは現在研究室の`data/raw/`、Macへの実転送は未実施、既存データを自動移動・削除しない
- Macの元データ保管先と配信bundleとstateは分離、元データ全体をDocker imageや配布ZIPへ含めない
- Macの確認時点の空きは174GiB、外付けSSDなし、空き40GiBを残す暫定計画なら作業予算は134GiB、実転送直前に再確認する
- 提供ファイルの圧縮時/展開後サイズは未確認、元データと配信コピーを含めて予算内に収め、全量が収まると仮定しない
- 必要曲は実験者がID集合で指定、モデルごとの上位曲から自動選択しない、初期選択と全比較条件で共通にする
- カタログ縮小は候補制約であり再学習ではない、曲順index・重み・近傍は維持し、全カタログのオフライン値と区別する
- 容量は元データ＋bundle＋state＋Docker image/cache＋更新中の旧新版の合計、ZIPやmedia生成物の併存分も加算する
- `bundle --dry-run`は対象ファイルのhash・実測bytes・曲数を表示、生成JSON・元データ保管・Docker・ログ容量は含まない
- `--max-bytes`は生成JSONを含む完成bundleの上限、超過時は途中物を公開せず停止する、曲やモデルを自動で削らない

```bash
# media処理後のconfigを指定、Macへのコピー前にも容量確認
.venv/bin/python -m src.interfaces.cli.online_bundle \
  --config /private/listening/config.json --dry-run
# 容量予算をbytesで明示し、新規出力へ梱包
.venv/bin/python -m src.interfaces.cli.online_bundle \
  --config /private/listening/config.json --output /private/bundle-v1 --max-bytes 1073741824
```

## Macで起動・更新

```bash
cd "$HOME/Downloads/music-recsys-mac-0.4.0"
# 旧版のログがある場合だけ実行、両Composeを停止して非上書き移動
bash deploy/mac.sh migrate-state
bash deploy/mac.sh start
bash deploy/mac.sh status
bash deploy/mac.sh stop
```

- Docker Desktopを起動し、版付きZIPを新しいフォルダへ展開する、旧コードへ重ねない
- Finderの`MusicRecsys.command`も利用可、URLは`http://127.0.0.1:8765`
- ホストの待受は127.0.0.1のみ、MacへのPython/Node導入は不要
- これは配信時の要件、media/bundleの準備CLIをMacで実行する場合は別途Python環境を用意する
- 移行対象は旧配布の`state/`または`~/OtoLab/state`、既存移行先への上書きを拒否する
- 旧ログを検出した場合は移行まで新規起動を拒否、`MUSIC_RECSYS_STATE`で保存先を明示変更できる
- 起動中の移行・実験中の更新は行わない、ログと再開条件は[オンライン評価](online-study.md#保存権限削除)
- `logs`はプロセス診断、参加者ログの管理は`sessions` / `export SESSION_ID`を使う

```bash
.venv/bin/python -m src.interfaces.cli.online_bundle \
  --config data/app/demo/config.json --study deploy/demo-study.json --output data/app/mac-demo-new
.venv/bin/python -m src.interfaces.cli.online_distribution \
  --root . --bundle data/app/mac-demo-new --version 0.4.0 \
  --output data/app/music-recsys-mac-0.4.0.zip
```

- 作成先は新規だけ、既存ZIP/bundleを上書きしない、元履歴・評価JSON・ログ・認証情報を配布へ混ぜない
- 公開Releaseへ添付するのは許諾済み合成デモだけ、実データ/実モデル/実音源は提供条件に従う

## Pythonでの開発

```bash
uv sync --locked --group app --group data --group models
(cd frontend && npm ci && npm run build)
# 未作成の場合だけ、合成デモを新規作成
.venv/bin/python -m src.interfaces.cli.online_prepare --demo --output data/app/demo
.venv/bin/python -m src.interfaces.http.server \
  --config data/app/demo/config.json --model-version demo-itemcf --port 8765
```

- 準備済みならprepareを省略、Vite開発は`cd frontend && npm run dev`、Node22.12以上が必要
- 研究者画面は`--audience researcher`の別プロセス、参加者向けに公開しない
- 実験計画は`--study <JSON>`で指定、[共通比較条件](online-study.md#比較実験)を適用する

## APIとUI

| 操作 | エンドポイント |
| --- | --- |
| 稼働案内 | GET /availability、モデルや個人ログを含めない |
| 状態/曲 | GET /api/status、GET /api/catalog |
| 開始/初期選択 | POST /api/sessions、PUT /api/profile |
| 初期試聴 | POST /api/previews、GET /api/preview-audio/{id}、推薦評価と分離 |
| 推薦/音声 | POST /api/recommendations、POST /api/playbacks、GET /api/audio/{id} |
| 行動/到達 | POST /api/events、POST /api/session/progress |
| 終了/本人ログ/削除 | POST /api/session/finish、GET /api/session/export、DELETE /api/session |

- 説明・同意→曲を試聴して初期選択→推薦/試聴→任意の満足度1問、モデル条件は秘匿する
- 外側は100dvh内に固定、同意文/楽曲一覧/PCキューだけ内部スクロール、ライト表示
- プレイヤーはシャッフル・前曲・再生/停止・次曲・リピートを等間隔に配置する
- 実際にplayした曲だけheardへ加える、初期選択を再生履歴と混同しない
- 「おまかせ」は現在未聴推薦、既聴・未聴を混ぜた総合推薦は未対応
- likeはログのみ、saveの参加者UIはなし、[オンライン評価](online-study.md)に従って記録する

## Music4Allの接続状況

| 対象 | 現状 |
| --- | --- |
| Popularity / ItemCF | 実学習の2条件 / 6条件が配信済み、採用条件は未確定 |
| ALS / BPR | export/load対応、BPR比較条件の実重みは実験内model/に保存、ALSの過去評価JSONは重みではない |
| BM25 / Content | 全件Validation対応、BM25の配信用重み保存済み、Content疎行列exportは未実装 |
| 実曲表示/音源 | Music4Allからデータ提供の連絡あり、ファイル受領・45,888学習IDとの対応付け・音源登録は未実施 |
| 合成デモ | 架空12曲・独自CC0 WAV、実Music4Allの音源や推薦評価と混ぜない |

- [Music4All公式申請先](https://sites.google.com/view/contact4music4all): contact4music4all@gmail.com
- [Onion v2](https://zenodo.org/records/15394646)の公開履歴取得と、原版clip/メタデータの提供・Web試聴許諾は別
- 提供案内の取得条件・招待被験者への認証付きWeb試聴範囲・帰属表示・期限を確認し、受領ファイルを照合して登録する
- 外部IDはTrackRefで保持、タイトル/artist一致だけで同一録音を確定せず、live/remix等の版を区別する
- Playback Adapterはavailable/unavailable/unknown/expiredを区別、URL取得をブラウザ再生成功としない

```bash
.venv/bin/python -m src.interfaces.cli.online_audit --config data/app/music4all/config.json \
  --audio-root /許可された音源保存先 --output data/app/music4all/audio-audit-new.json
.venv/bin/python -m src.interfaces.cli.online_media --config data/app/music4all/config.json \
  --metadata /private/tracks.csv --audio-root /private/clips \
  --permission /private/permission.json --items /private/study-items.json \
  --output data/app/music4all-listening
```

- metadataは`item_id,title,artists`、複数artistは`|`区切り、音源stemはitem_idと一致させる
- `--items`は重複なし楽曲IDのJSON配列、許諾IDの部分集合だけをコピーし、カタログ・推薦候補も同じ集合へ限定する
- `--items`省略時は許諾ID全件、選んだ全曲でメタデータと音源が必要、欠損は別データで埋めない
- permissionは`web_preview=true`・`access_scope=restricted-study`・確認済み`evidence_ref`・timezone付き`expires_at`・明示`item_ids`
- 許諾JSONは確認済み条件の記録であり、それ自体が法的許諾を発生させるものではない
- mediaは新bundleへコピー/hash検証、モデルの絶対参照を残すので別ホストへ移す場合はbundle化する
- 外部サービスPoCは既定と分離、`catalog_poc` / `jamendo_poc` / `listenbrainz_poc --help`で明示操作する
- JamendoはAPI承認待ち、ListenBrainz履歴は音源配信権を含まず、未許諾の外部データを学習可能と推測しない
- 公式資料: [Jamendo tracks/規約](https://developer.jamendo.com/v3.0/tracks) / [規約](https://devportal.jamendo.com/api_terms_of_use) / [ListenBrainz](https://listenbrainz.org/data/) / [FMA](https://github.com/mdeff/fma)

## Cloudflare Tunnel / Accessでの遠隔接続

- [Cloudflareの手順](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/self-hosted-public-app/)でnamed tunnelとAccessアプリを用意、対象hostname全体を保護し被験者だけAllowにする
- Macのホストへcloudflaredを導入、`deploy/cloudflared.example.yml`をprivate領域へコピーしてUUID・credentials・hostname・TEAM/AUDを設定する
- 以下のTEAM/AUD・Origin/hostはAccessアプリと一致させる、認証情報をGit・Vite変数・ブラウザへ含めない

```bash
# Macの非公開設定から実値を指定、保持日数は説明文に合う値を決める
export MUSIC_RECSYS_ACCESS_TEAM=REPLACE_WITH_TEAM
export MUSIC_RECSYS_ACCESS_AUD=REPLACE_WITH_AUD
export MUSIC_RECSYS_ALLOWED_ORIGINS=https://music-recsys.example.com
export MUSIC_RECSYS_ALLOWED_HOSTS=music-recsys.example.com
export MUSIC_RECSYS_RETENTION_DAYS=REPLACE_WITH_DAYS
bash deploy/mac.sh start-remote
cloudflared tunnel --config /absolute/private/cloudflared.yml run
```

- `start-remote`は認証/保持日数の必須設定と未認証401を確認する、`start`はローカル確認専用、Tunnelは自動起動しない
- UI/音声/APIは同一Origin、全パスでAccess JWTを検証、更新APIはOrigin/専用ヘッダー、cookieはHttpOnly/SameSite/Secure
- API/音声はno-store、Access認証と研究参加の同意・参加コードを分離、`MUSIC_RECSYS_API_TOKEN`は本構成では設定しない
- Macのスリープ・回線切断・Docker/Tunnel停止中は利用できない、常時稼働する外部案内ページは置かない
- `/availability`も認証対象、次回予定は`MUSIC_RECSYS_NEXT_OPEN_AT`で明示、timezone付きISO日時、未指定/過去は予定未定
- ローカル合成デモはprepareで作成、ブラウザのみの確認はpublic_demo export＋`npm run build:demo`、通常buildはFastAPIへ接続する
- 遠隔接続・Mac実機・実曲再生は実地確認が必要、既存Cloudflare上のPages/Workersは本リポジトリの設定削除だけでは停止されない
