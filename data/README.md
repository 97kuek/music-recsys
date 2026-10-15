# データ

- 本体・中間生成物・実験結果はGit管理外
- 取得・再開・保存先の変更は[運用手順](../docs/lab-operation.md)
- 保存構成は[実装構成](../docs/architecture.md)、評価入力は[評価仕様](../docs/listening-evaluation.md)

| 出典 | 内容 |
| --- | --- |
| Zenodo record 15394646 / Music4All-Onion v2 | 再生履歴・重み付きタグ |
| userid_trackid_timestamp.tsv.bz2 | 2,211,449,511 bytes / MD5 dfe82201036765f7463e6f3ce3d0f991 |
| id_tags_dict.tsv.bz2 | 14,817,003 bytes / MD5 9c1ca4a3e04a9696b9449ec54ac40cd5 |

- 配布情報は取得時のrecord.jsonに保存
- 再生履歴の列はuser_id・track_id・timestamp
- 元IDは文字列を維持、時刻のタイムゾーンは未確認
- 曲名・アーティスト名・ユーザー属性・音源はこの取り込みの対象外
- `lab_data` はサイズ・MD5・加工物SHA-256を照合
- ready.jsonの公開後も、再利用時に全保存物のhashを確認
