# ftx1_audio_swap

八重洲無線 FTX-1は、デュアルバンド受信時のMAINバンド/SUBバンドの音声をUSBオーディオのLEFTチャネル/RIGHTチャネルに出力しますが、どちらのチャネルに出力するかはバンド設定の組み合わせで決まり、パネルの操作バンドの選択には従いません（下表）。このツールはrigctld経由でリグの状態を読み、WSJT-X（入力：USB Audio Device / Mono）に受信したい側の音声が届くように、必要に応じて自動的にLEFT/RIGHTを入れ替えます。チャネルの入れ替えにはオープンソースソフトウェアのEqualizer APOを使います。

## USBオーディオの出力（実測）

| SUB   | MAIN       | LEFT (=Mono) | RIGHT |
| ----- | ---------- | ------------ | ----- |
| HF/50 | HF/50      | 操作バンド側 | 無音  |
| HF/50 | VUHF       | SUB          | MAIN  |
| VUHF  | HF/50/VUHF | MAIN         | SUB   |

VUHF = 144/430MHz帯（判定の境目は100MHz。`--vuhf-mhz`で変更可）。

## 受信したい側の選択（--mode）

| モード         | 受信したい側 |
| -------------- | ------------ |
| follow（既定） | 操作バンド側 |
| main           | 常にMAIN     |
| sub            | 常にSUB      |

このツールは、受信したい側の音声がRIGHTチャネルにあるときだけEqualizer APOのconfigフォルダのswap.txtに`Copy: L=R R=L`を自動的に書き込むことによりLEFT/RIGHTを入れ替え、それ以外のときはswap.txtを空にしてパススルーにします。

follow以外の設定ではWSJT-Xの周波数表示と受信信号が一致しないケースが発生するので注意してください。
また、follow以外の設定では受信したい側の音声がUSBオーディオに出ないケース（両方HF/50で、main/subモードの指定と反対側を操作中）があり、その場合は入れ替えずにログへ警告を出します。

## 必要なもの

- Windows 10 / 11
- FTX-1（CAT動作にはMAINファームウェアVer.1.08以上が必要です）
- Python 3.6以上（標準ライブラリのみ。python/pythonwにPATHが通っていること）
- HamlibのrigctldがFTX-1に接続して起動していること（既定 127.0.0.1:4534）。
  WSJT-Xなどもrigctldで制御するように設定しておく必要があります。
- Hamlibは4.7.3以上を推奨します（2026年9月時点では、開発版の4.7.3~rcを
  https://hamlib.sourceforge.net/snapshots-4.7/ から入手できます）。
  4.7.0では（4.7.1 / 4.7.2は未確認）、操作バンドがW-FM（FM放送のメモリーなど）のときにrigctldがモードを読み取れずエラーになり、
  WSJT-Xなどが接続していると、rigctldが接続を開き直すことを繰り返します。
- Equalizer APO ( オープンソース・オーディオイコライザー )
  - ダウンロードしてインストールする
  - インストール時に表示されるDevice Selectorで、USB Audio Deviceのマイク（Capture devices）にチェックを入れてOKボタンを押し、Windowsを再起動する（あとから変更するときはEqualizer APOのフォルダのDeviceSelector.exeを使う）
  - `config.txt`の中身：初期設定はすべて削除し、`Include: swap.txt` の1行のみに変更しておく
  - Equalizer APO Configuration Editorを起動し、`config.txt`タブの`Include: swap.txt`の電源ボタンがオン（Power on）になっていることを確認する（起動時に「APO not installed to device」のポップアップが表示された場合は No を押す）
  - configフォルダに自分のユーザーの書き込み権限（「変更」）を付与
  - Windowsのサウンド設定でUSB Audio Deviceのマイクの「オーディオの強化」を「デバイスの既定の効果」にしておく（オフにするとEqualizer APOが動かない）
- `ftx1_audio_swap.py`と`start_ftx1_audio_swap.vbs`は同じフォルダに置く

rigctldの起動例（COMポート、TCPポートは環境に合わせてください）:

```
rigctld -m 1051 -r COM5 -s 38400 -t 4534
```

FTX-1のモデル番号は1051です。
COMポートはEnhanced COM Port（CAT-1）を指定します。

## 使い方

### 動作確認

rigctldを起動した状態で、判定を1回だけ表示します（swap.txtは書き換えません）:

```
python ftx1_audio_swap.py --dry-run --once
```

※ `pythonw` ではなく `python` で実行してください（pythonwでは画面に何も表示されません。ログファイルには記録されます）。

正常なら次の例のように表示されます:

```
2026-09-25 15:20:01,234 INFO start: mode=follow rigctld=127.0.0.1:4534 interval=2.0s vuhf>=100.0MHz swap_file=C:\Program Files\EqualizerAPO\config\swap.txt (registry) [DRY RUN]
2026-09-25 15:20:01,250 INFO connected to rigctld
2026-09-25 15:20:01,310 INFO VS=MAIN MAIN=144.460000MHz SUB=7.074000MHz target=MAIN -> SWAP (target on RIGHT)
```

各行の見方:

- 1行目 `start:`：`swap_file=` がEqualizer APOのconfigフォルダのswap.txtになっていることを確認します。カッコ内はパスの決め方です（`registry`＝レジストリから取得、`default`＝既定のパス、`command line`＝`--swap-file` で指定）。`default` のときは、Equalizer APOのインストール先と合っているか確認してください。
- 2行目 `connected to rigctld`：rigctldに接続できたことを表します。
- 3行目：判定の結果です。
  - `VS=`：操作バンド側
  - `MAIN=` / `SUB=`：それぞれの周波数
  - `target=`：受信したい側
  - `->` のあと：判定

| 判定                                                       | 意味                                                                                             |
| ---------------------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| `PASS (target on LEFT)`                                    | 受信したい側がLEFTに出ている。swap.txtは空にする（入れ替えなし）                                 |
| `SWAP (target on RIGHT)`                                   | 受信したい側がRIGHTに出ている。swap.txtに `Copy: L=R R=L` を書く                                 |
| `WARNING ... PASS (target audio not available on USB ...)` | 両方HF/50のまま、main/subモードで反対側を操作中。受信したい側の音声はUSBに出ないので入れ替えない |

リグ側でバンドの組み合わせ（HF/50とVUHF）や操作バンドを変えて何回か実行し、判定が「USBオーディオの出力（実測）」の表と合っていれば正常です。

うまくいかないとき:

- `WARNING rigctld: ... (keeping current swap state, retrying)` と出て終わる：rigctldに接続できていません。rigctldが起動しているか、`--host` / `--port` が合っているか確認してください（`--once` なので再接続はせずに終了します）。
- 常駐起動後、ログに `ERROR cannot write ...swap.txt ... (check folder write permission)` が出る：configフォルダへの書き込み権限がありません（dry-runではswap.txtを書かないので、このエラーは出ません）。
- ログでは判定どおりにswap.txtを書き換えているのに、入れ替えが効かない：Windowsの大型アップデートの後に、Equalizer APOのデバイス設定が外れることがあると言われています。Equalizer APOのフォルダのDeviceSelector.exeで、USB Audio Deviceのマイク（Capture devices）をもう一度選び直し、Windowsを再起動してください。

### 常駐起動方法

`start_ftx1_audio_swap.vbs` をダブルクリックします。必要に応じて、モードはvbs内の`MODE`で、その他のオプションは`EXTRA_OPTS`で指定します。

ログオン時に自動起動するには、`start_ftx1_audio_swap.vbs` のショートカットを作り、スタートアップフォルダ（`Win+R` → `shell:startup`）に置きます。

### 停止方法

タスクマネージャーで該当の`pythonw.exe`を終了します（ほかにも`pythonw.exe`が動いているときは、「詳細」タブの列見出しを右クリックして「コマンドライン」列を表示し、`ftx1_audio_swap.py`のものを見分けてください）。停止後もswap.txtは最後の状態のまま残ります。入れ替えが残って困るときは、swap.txtの中身を削除して空にしてください。

## オプション

| オプション          | 既定値           | 内容                                                                                                                        |
| ------------------- | ---------------- | --------------------------------------------------------------------------------------------------------------------------- |
| `--mode`            | follow           | follow / main / sub                                                                                                         |
| `--host` / `--port` | 127.0.0.1 / 4534 | rigctldの接続先                                                                                                             |
| `--interval`        | 2.0              | ポーリング間隔（秒）                                                                                                        |
| `--vuhf-mhz`        | 100              | この周波数以上をVUHFとみなす                                                                                                |
| `--swap-file`       | 自動             | swap.txtのパス。未指定時はレジストリ`HKLM\SOFTWARE\EqualizerAPO\ConfigPath`、なければ`C:\Program Files\EqualizerAPO\config` |
| `--dry-run`         | -                | 判定をログに出すだけでswap.txtを書かない                                                                                    |
| `--once`            | -                | 1回だけ判定して終了                                                                                                         |

## このツールの動作の詳細説明

- rigctldへは専用のTCP接続を1本張り、`\set_vfo_opt 1`（この接続だけVFO明示）にしてから、`v`・`f Main`・`f Sub`を問い合わせます。WSJT-Xなどには影響しません。
- swap.txtは判定が変わったときだけ自動的に書き換えます。
- rigctldに接続できない間は、swap.txtを変えずに再接続を試み続けます。
- ログ: `ftx1_audio_swap.py`と同じフォルダの`logs\ftx1_audio_swap.log`（1MB×4世代でローテーション）。起動時、rigctldへの接続時、判定が変わったとき、swap.txtを書き換えたとき、エラーの発生時に記録します。

## ライセンス

- 本ソフトウェアは無償で配布します。個人・団体を問わず、自由に使用できます。
- ソースコードの改変、改変したものの再配布も自由です。再配布の際は、元の著作権表示を残してください。
- 本ソフトウェアは無保証です。使用によって生じたいかなる損害（オーディオ設定の変更や誤動作によるものを含む）についても、作者は責任を負いません。

「FTX-1」は八重洲無線株式会社の製品名です。本ソフトウェアは同社とは関係のない個人の制作物です。
無線機の状態の読み取りには Hamlib（rigctld）を、音声チャネルの入れ替えには Equalizer APO を使用しています。どちらも本ソフトウェアには含まれていません。

## 変更履歴

- Ver.1.0 初版

---

(c) 2026 Takeshi Mishima JK1VUZ
