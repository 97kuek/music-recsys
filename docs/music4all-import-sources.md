# Music4All-Onionの取り込み元確認

- 確認日：2026-10-12。対象：Music4All-Onionの[Zenodo record 15394646](https://zenodo.org/records/15394646)。
- 公開APIの46ファイルと配布READMEを確認。基本楽曲カタログ・ユーザー属性のファイルは、このレコードには含まれない。[配布API](https://zenodo.org/api/records/15394646)

| 取り込み元 | 確認状態 | 確認できた内容 |
| --- | --- | --- |
| `userid_trackid_timestamp.tsv.bz2` | 取得済み・実ファイル確認済み | `user_id`, `track_id`, `timestamp`。楽曲・ユーザーの元IDと再生時刻。楽曲名・ユーザー属性の列はない |
| `id_tags_dict.tsv.bz2` | 取得済み・実ファイル確認済み | 実ヘッダーは `id`, `(tag, weight)`。後者はタグをキー、重みを値とするPython辞書の文字列表現。JSONとは扱わない |
| `id_genres_tf-idf.tsv.bz2` | 公開ファイル・仕様を配布READMEで確認 | Music4AllジャンルのTF-IDF特徴。基本楽曲カタログではない |

- 上表の出典：[配布ファイル・README](https://zenodo.org/records/15394646/files/README.md)。実ファイルのチェックサムは[配布API](https://zenodo.org/api/records/15394646)に記載。
- タグファイルのサイズ14,817,003 bytesと公式MD5 `9c1ca4a3e04a9696b9449ec54ac40cd5`を照合済み。SHA-256は `8a62f5373ae386ea9de3c8c34391559be49a8f925579a509992485f66af84bdb`。ファイル本体はGit管理外。

| タグファイルの実測 | 結果 |
| --- | --- |
| 行数 / 一意な楽曲ID数 | 109,269 / 108,286 |
| 複数回出現する楽曲ID | 974 ID。965 IDは2回、9 IDは3回。追加の出現は合計983件 |
| 重複IDの内容 | 辞書順序を除いて全て同一。異なる内容を持つ競合IDは0 |
| タグの重み | 3,677,133エントリー、全て整数1〜100 |
| 空のタグ辞書 | 20,397行 |
| イベント側の楽曲IDとの照合 | 56,512曲中56,155曲のIDがタグファイルに存在。空辞書を含む |

- 実測元：[公開タグファイル](https://zenodo.org/records/15394646/files/id_tags_dict.tsv.bz2)。辞書は `ast.literal_eval` で読み、キーをソートしたJSONのSHA256で重複内容を比較。
- 重複の原因について、公式の説明は未確認。楽曲の同一性や別ID間の対応付けは判断していない。

| 元のMusic4Allのファイル | 原著に記載された内容（実CSVヘッダーは未確認） |
| --- | --- |
| `id_information.csv` | 楽曲ID、アーティスト名、楽曲名、アルバム名 |
| `id_metadata.csv` | 楽曲ID、Spotify ID、Spotify由来の属性 |
| `id_genres.csv` | 楽曲ID、ジャンル一覧 |
| `id_tags.csv` | 楽曲ID、タグ一覧 |
| `id_lang.csv` | 楽曲ID、歌詞の言語 |
| `listening_history.csv` | 元のMusic4Allユーザーの再生履歴 |

- 元のMusic4Allの公式サイトは、取得方法を提供者へのメール申請として案内している。申請・メール送信は実施していない。[公式サイト](https://sites.google.com/view/contact4music4all)
- 上表は[Music4All原著のTable II](https://www.din.uem.br/yandre/IWSSIP_2020_Music4All.pdf)の説明。未取得ファイルのカラム名・型・欠損値を確定仕様として扱わない。

| ID・ユーザー属性の確認 | 結果 |
| --- | --- |
| Onion再生履歴の由来 | Music4All楽曲とLFM-2b楽曲をSpotify URIで対応付け、56,512曲に対応するLFM-2bの再生履歴を採用。[Onion原著 §4.6](https://humrec.github.io/publication/moscati-cikm-2022/moscati-cikm-2022.pdf) |
| 元のMusic4Allユーザーとの対応 | 元のMusic4Allは15,602ユーザー、Onionは119,140ユーザー。ユーザーIDの対応表・同一ID空間であることは未確認。[Music4All公式](https://sites.google.com/view/contact4music4all)、[Onion配布元](https://zenodo.org/records/15394646) |
| LFM-2bのユーザー属性 | 公式説明に `users.tsv.bz2` の `user_id`, `country`, `age`, `gender`, `creation_time` が記載されている。現在はライセンス上の問題により配布終了と案内されており、実ファイルは未取得。[LFM-2b公式](https://www.cp.jku.at/datasets/LFM-2b/) |
| OnionイベントIDと外部メタデータの対応 | Spotify URIでの楽曲対応は論文で確認済み。未取得カタログとの実ID照合、LFM-2bユーザー属性との実ID照合は未実施 |

- 現在取得済みのファイルだけでは、楽曲名・アーティスト名・年齢・国・性別を取り込めない。
- 非公式ミラーのメタデータは、今回の仕様確認・取り込み元に採用していない。
