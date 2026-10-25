# UIとアプリ運用

## 現行の導線と確認範囲

- 参加者の固定入口は[Pages](https://music-recsys-app.pages.dev/)。設定上はAccess認証→Pages/Functions→MacのQuick Tunnel→FastAPI
- 接続先はMusic4All実曲アプリ、参加者モード、`music4all-itemcf-cosine-20`。研究上の採用モデルを確定するものではない
- 曲名/artist・5曲・30秒試聴・復帰後再接続は利用者報告で確認。新規アドレスの認証コード着信は未確認
- Like/任意回答の保存・本人/期限削除・別認証ID分離の実機確認は未完了。[TODO](todo.md)を参照
- UIを修正・ZIPを作成しただけではMacへ反映されない。このリポジトリの操作から稼働中Macを再起動しない

```mermaid
flowchart LR
    U[参加者 / Pages URL] --> A[Cloudflare Access / メール認証]
    A --> P[React / 利用説明と接続案内]
    P --> F[同一Origin api / Pages Functions]
    F --> K[KV / 接続先と期限]
    F --> T[Quick Tunnel]
    T --> M[Mac / FastAPI・SQLite・許諾済み音源]
    M -. 起動時登録と定期更新 .-> K
```

- モデル・音源・生ログをPages/KVへ置かない。登録先失効時は休止案内を表示し、参加者へ内部URLやtokenを公開しない
- 外部公開・ポート開放は個人Macで確認する。研究室サーバーを公開する経路にしない

## 参加者の操作

```mermaid
flowchart LR
    C[利用説明へ同意] --> S[選曲 / 途中再開]
    S --> R[おすすめ5曲 / 自由試聴・曲移動・Like]
    R --> Q[リスト満足度1〜5 / 任意]
    Q --> D[Like曲の新規発見 / 任意]
    D --> ACK[操作と回答のACK]
    ACK --> N[次の条件 / 終了]
```

- 参加者は条件・モデルを直接選ばず、モデル名を秘匿する。研究者の計画JSONで版・割当・共通カタログを固定する
- 初期選択の試聴を推薦後履歴へ加算しない。比較内では同じ固定入力を使い、後の条件へ途中嗜好変化を持ち越さない
- 曲単位の必須フォームは置かない。前/次/カード/キューの移動を同じイベント契約で記録する
- 満足度とDiscoveryは同じ画面。回答前は試聴へ戻れ、未回答と明示的な該当なしを区別する
- 送信後はリストを閉じ、同じリストへ再回答しない。再送だけを許可し、次の条件へ未送信操作を持ち越さない
- 「？」から説明と本人削除へ進む。退室と削除は別操作、次セッションへ選曲/Likeを自動継承しない
- 旧プロフィール列は読込み・削除互換のため保持する。新しい推薦へ導出・更新・利用しない
- LLM/Agent調整UI、Repeat/混合総合の参加者比較は未実装。現在の通常更新をAgent調整と呼ばない

## ローカル起動

```bash
# 個人MacでZIPを展開し、Docker Desktopを起動して実行
bash deploy/mac.sh start
bash deploy/mac.sh status
bash deploy/mac.sh logs
bash deploy/mac.sh stop
```

- 標準は127.0.0.1:8765の参加者アプリ。研究室のオフライン経路にはDockerを要求しない
- 原本bundle・音源・model manifest・表示metadataを検証し、別モデルや合成デモへ暗黙に切り替えない
- 合成デモは操作確認専用で8秒音源。実曲の研究結果・10秒試聴指標の代用にしない
- `researcher`は別のローカルプロセス/ホスト名で起動する。参加者側へ比較詳細・モデル情報・OpenAPIを公開しない
- 更新は停止確認とstate退避の後に明示実施する。コードZIPの更新と既存DBの移行を同一視しない
- Mac側で起動待ちを20分へ調整した版があるが、リポジトリの待機設定と同じ確認済み版とは扱わない

## 固定URLと認証

```bash
# Macで必要な非公開設定を用意して実行
bash deploy/mac.sh start-pages
bash deploy/mac.sh researcher
```

- Pagesの中継は`deploy/pages/`、Mac登録は`deploy/pages.sh`。Quick Tunnelの接続先を期限付きで更新する
- 本番Accessは事前登録不要のOne-time PIN、プレビューは研究者本人限定という設定。コード未着の確認を残す
- 外部認証のissuer・audience・署名・期限・host/originを検査し、認証IDごとに参加者を分離する
- 遠隔起動ではAccessを使い、固定API tokenによる代用をしない。研究者HTTPを外部へ公開しない
- 秘密値はMacの非公開設定へ置く。`.env`・登録token・認証設定の内容をZIP・Git・公開ログへ入れない
- 現行実曲stateは`~/music-recsys/music4all-pages-state-v1`、確認時の保持期間は1日。デモstateと結合しない
- 旧Worker/ngrok経路は代替用の明示モード。現行Pagesの確認結果を代替経路の確認済み扱いにしない

## 保存・保持・削除

| 対象 | 保存・権限 | 削除・制約 |
| --- | --- | --- |
| 参加者状態 | MacのSQLite、OS所有者権限、同意後の永続化 | 本人削除・期限削除、対応IDと関連sessionも対象 |
| 生ログexport | Macの管理CLI、新規ファイル、非公開領域 | 管理exportsへ期限/本人削除を伝播 |
| 仮名化集計 | ローカル研究者画面/API、読取り専用CLI | 手動コピー・OS backupは別の保持/削除手順 |
| モデル・音源 | 検証済みbundle、manifest/hash・利用条件 | コードの掃除へ混ぜず、出典と許諾に従う |

- 同意UIはlocal-ui-v8。保持日数未設定では新規参加できず、旧ログschemaの読込み互換を維持する
- 停止中は削除タイマーが動かず、起動時に期限切れを処理する。SQLite secure_deleteはSSD/backupの完全消去保証ではない
- DB暗号化・自動backup・管理者アクセス監査は未実装。MacのOS権限と手動コピー管理を別途確認する
- 音源は期限・許可された配信範囲を確認し、期限なしの許諾も出典のある記録で識別する。欠測日付から無期限を推定しない

```bash
bash deploy/mac.sh sessions
bash deploy/mac.sh export SESSION_ID
.venv/bin/python -m src.interfaces.cli.online_logs \
  --database /private/sessions.sqlite list
```

- 本人向け簡略exportと研究用生ログexportは別。簡略版だけで再生可否・usageを集計できる扱いにしない
- source SQLiteと成果物を一緒に保管し、実行中ジョブと競合する削除・移動をしない

## 比較実験と配布

- 最大5人、通常3条件、主指標Satisfaction。3〜5人の予備確認は理解・負担・欠測を調べる段階
- 同じ入力・カタログ・音源・K・試聴長・質問・画面を使い、均衡順序・休憩・曲の重複方針を計画に記録する
- 再訪は同じ参加者内の平均とし、sessionを独立した人へ数え直さない
- required_route不一致は提示を拒否する。BPRの未知ユーザー補完をBPRそのものの条件へ混ぜない
- 配布物は`data/app/releases/music-recsys-mac.zip`とchecksumへ集約する。8モデル・実曲名catalog・metadata・許諾記録を含み、音源・秘密ファイル・SQLite・学習入力は含めない
- 新規準備用のbundleを既存Macの実音源bundleやstateへ上書きしない。既存環境ではコード更新とbundle/DBの移行を分ける
- 旧ZIPを削減する前に、後継版で必要なコード・metadata・モデル・許諾記録が揃うことを検証する
- この整理からMac上のstate・展開済みファイル・実行プロセスを削除しない
- 研究者HTTP・Lang系Adapterは任意の研究用接続。参加者向けの実API稼働確認や本実験完了とは扱わない
