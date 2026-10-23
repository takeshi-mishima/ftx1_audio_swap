# ftx1_audio_swap Ver.2.2

八重洲無線 FTX-1は、デュアルバンド受信時のMAINバンド/SUBバンドの音声をUSBオーディオのLEFTチャネル/RIGHTチャネルに出力しますが、どちらのチャネルに出力するかはバンド設定の組み合わせで決まり、パネルの操作バンドの選択には従いません（下表）。このツールはrigctld経由でリグの状態を読み、WSJT-X（入力：USB Audio Device）に受信したい側の音声が届くように、必要に応じて自動的にLEFT/RIGHTを入れ替えます。2つのWSJT-Xを使うデュアルバンド運用では、WSJT-X #1（入力：Left）にMAIN、WSJT-X #2（入力：Right）にSUBの音声が常に届くようにします（fixedモード、既定）。WSJT-Xの受信チャネルは「Mono」ではなく「Left」「Right」に設定してください（「Mono」は、WSJT-Xが1チャネルで開いてWindowsにステレオからの変換を任せる設定で、LEFTだけを使うとは限りません。LとRが混ざると、RIGHTの漏れがLEFTに入ります）。また、WSJT-Xに本来の受信音ではない音声（送信音声の混入、HF/50同士の漏れ、CWモードの側への混入）が入ってデコードされないように、該当するチャネルを自動的に無音にします（後述の「漏れ対策（ミュート）」）。このツールはチャネルの入れ替えとミュートの判断を行い、実際の処理にはオープンソースソフトウェアのEqualizer APOを使います。

## USBオーディオの出力（実測）

| SUB   | MAIN       | LEFT         | RIGHT                                  |
| ----- | ---------- | ------------ | -------------------------------------- |
| HF/50 | HF/50      | 操作バンド側 | 操作バンド側の音声の弱い複製（約-21dB） |
| HF/50 | VUHF       | SUB          | MAIN                                   |
| VUHF  | HF/50/VUHF | MAIN         | SUB                                    |

VUHF = 144/430MHz帯（判定の境目は100MHz。`--vuhf-mhz`で変更可）。

MAIN/SUBとも HF/50 のときは、FTX-1はデュアル受信をせず、操作バンドでない側はRFを受信しません。それでもRIGHTには、LEFTと同じ音声が約-21dB低いレベルで出力され、WSJT-Xでデコードされます（実測）。このツールはこのRIGHTを無音にします。

## 受信したい側の選択（--mode）

| モード        | LEFTに届ける音声 | RIGHTに届ける音声 |
| ------------- | ------------------------ | ----------------- |
| fixed（既定） | 常にMAIN                 | 常にSUB           |
| follow        | 操作バンド側             | （指定なし）      |
| main          | 常にMAIN                 | （指定なし）      |
| sub           | 常にSUB                  | （指定なし）      |

fixedは、WSJT-Xを2つ起動して、#1をMAIN、#2をSUBの担当に固定する運用向けです（ftx1_rigwrapを `--listen 4535:main --listen 4536:sub` で起動し、#1は4535、#2は4536に接続します）。MAIN/SUBを入れ替えても各WSJT-Xの担当バンドは変わらないので、モードやトランスバータの周波数オフセットなど、WSJT-Xごとの設定をそのまま使えます。

このツールは、入れ替えが必要なときだけEqualizer APOのconfigフォルダのswap.txtに`Copy: L=R R=L`を自動的に書き込むことによりLEFT/RIGHTを入れ替え、それ以外のときはswap.txtを空にしてパススルーにします（無音にするチャネルがあるときは、同じ1行に`0.0`を含めます。後述）。入れ替える条件は、fixedでは「LEFTがSUB、またはRIGHTがMAIN」のとき、follow/main/subでは「受信したい側の音声がRIGHTにある」ときです。

fixedで、MAIN/SUBとも HF/50 のときは、操作バンド側の音声だけがUSBに出ます（もう一方のチャネルは上記の漏れなので無音にします）。操作バンドがMAINなら#1だけ、SUBなら#2だけが受信できます（SUBの音声がRIGHTへ移るよう入れ替えます）。

WSJT-Xの周波数表示と受信音声が一致するかどうかは、WSJT-XがCATで読む側と、このツールが選ぶ音声の側が同じかどうかで決まります。

- fixed：WSJT-X #1をftx1_rigwrapの4535（main）、#2を4536（sub）に接続すると、表示と音声が一致します（#1＝MAIN、#2＝SUB）。
- follow：WSJT-Xが操作バンドを読む構成（ラッパーを使わずrigctldに直接接続、またはftx1_rigwrapのfollow）と組み合わせると一致します。
- main / sub：WSJT-Xが操作バンドを表示している構成と組み合わせると、操作バンドが反対側のときに、周波数表示と受信音声が一致しなくなるので注意してください（例：main、操作バンドがSUB → 表示はSUBの周波数、音声はMAIN）。

また、MAIN/SUBとも HF/50 のときは、本来の受信音は操作バンド側の音声しかUSBオーディオに出ません（もう一方のチャネルは漏れなので、このツールが無音にします）。main / subでは、指定した側と反対側を操作中だと受信したい側の音声が出ないので、入れ替えずにログへ警告を出します。fixedでは、操作バンドでない側のWSJT-Xが無音になります（警告は出しません）。

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
2026-10-09 00:02:28,771 INFO ftx1_audio_swap 2.2 start: mode=fixed rigctld=127.0.0.1:4534 interval=2.0s vuhf>=100.0MHz swap_file=C:\Program Files\EqualizerAPO\config\swap.txt (registry) [DRY RUN]
2026-10-09 00:02:28,830 INFO connected to rigctld
2026-10-09 00:02:29,015 INFO VS=MAIN MAIN=50.260000MHz SUB=144.460000MHz target=MAIN=L SUB=R -> PASS (MAIN on LEFT, SUB on RIGHT)
```

このあと、無音にするチャネルがあるときは `mute: raw L [TX]` のような行（`--dry-run`では `[DRY RUN] swap.txt would be: ...` の行も）が表示されます。モードがCWの側があるときは `mode: SUB is CW` が表示されます。

この例は既定のfixedモードです。`--mode follow` などを指定したときは、`target=` と判定の表示が下の表のようになります（例：`target=MAIN -> SWAP (target on RIGHT)`）。

各行の見方:

- 1行目 `start:`：プログラム名とバージョン、`mode=`（使用中のモード）が表示されます。`swap_file=` がEqualizer APOのconfigフォルダのswap.txtになっていることを確認します。カッコ内はパスの決め方です（`registry`＝レジストリから取得、`default`＝既定のパス、`command line`＝`--swap-file` で指定）。`default` のときは、Equalizer APOのインストール先と合っているか確認してください。
- 2行目 `connected to rigctld`：rigctldに接続できたことを表します。
- 3行目：判定の結果です。
  - `VS=`：操作バンド側
  - `MAIN=` / `SUB=`：それぞれの周波数
  - `target=`：受信したい側（fixedでは `MAIN=L SUB=R`＝LEFTにMAIN、RIGHTにSUB）
  - `->` のあと：判定

| 判定                                                       | 意味                                                                                             |
| ---------------------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| `PASS (target on LEFT)`                                    | 受信したい側がLEFTに出ている。swap.txtは空にする（入れ替えなし）                                 |
| `SWAP (target on RIGHT)`                                   | 受信したい側がRIGHTに出ている。swap.txtに `Copy: L=R R=L` を書く                                 |
| `WARNING ... PASS (target audio not available on USB ...)` | 両方HF/50のまま、main/subモードで反対側を操作中。受信したい側の音声はUSBに出ないので入れ替えない |
| `PASS (MAIN on LEFT, SUB on RIGHT)`                        | fixed：すでにLEFT＝MAIN、RIGHT＝SUB。swap.txtは空にする（入れ替えなし）                          |
| `SWAP (SUB on LEFT, MAIN on RIGHT)`                        | fixed：LEFT＝SUB、RIGHT＝MAIN。swap.txtに `Copy: L=R R=L` を書く                                 |
| `PASS (MAIN on LEFT only (both HF/50))`                    | fixed：両方HF/50で操作バンドがMAIN。MAINだけがLEFTに出ている。入れ替えなし                       |
| `SWAP (SUB on RIGHT only (both HF/50))`                    | fixed：両方HF/50で操作バンドがSUB。SUBをRIGHTへ移すために入れ替える                              |

リグ側でバンドの組み合わせ（HF/50とVUHF）や操作バンドを変えて何回か実行し、判定が「USBオーディオの出力（実測）」の表と合っていれば正常です。fixedでは、どの組み合わせでも、判定後にLEFT＝MAIN、RIGHT＝SUB（両方HF/50のときは操作バンド側だけ）になっていれば正常です。

うまくいかないとき:

- `WARNING rigctld: ... (keeping current swap state, retrying)` と出て終わる：rigctldに接続できていません。rigctldが起動しているか、`--host` / `--port` が合っているか確認してください（`--once` なので再接続はせずに終了します）。
- 常駐起動後、ログに `ERROR cannot write ...swap.txt ... (check folder write permission)` が出る：configフォルダへの書き込み権限がありません（dry-runではswap.txtを書かないので、このエラーは出ません）。
- ログでは判定どおりにswap.txtを書き換えているのに、入れ替えが効かない：Windowsの大型アップデートの後に、Equalizer APOのデバイス設定が外れることがあると言われています。Equalizer APOのフォルダのDeviceSelector.exeで、USB Audio Deviceのマイク（Capture devices）をもう一度選び直し、Windowsを再起動してください。

### 常駐起動方法

`start_ftx1_audio_swap.vbs` をダブルクリックします。必要に応じて、モードはvbs内の`MODE`で、その他のオプションは`EXTRA_OPTS`で指定します。

ログオン時に自動起動するには、`start_ftx1_audio_swap.vbs` のショートカットを作り、スタートアップフォルダ（`Win+R` → `shell:startup`）に置きます。

### 停止方法

タスクマネージャーで該当の`pythonw.exe`を終了します（ほかにも`pythonw.exe`が動いているときは、「詳細」タブの列見出しを右クリックして「コマンドライン」列を表示し、`ftx1_audio_swap.py`のものを見分けてください）。停止後もswap.txtは最後の状態のまま残ります。入れ替えやミュート（後述）が残って困るときは、swap.txtの中身を削除して空にしてください（Ctrl+Cなどで正常に終了したときは、ミュートだけを解除して入れ替えの状態を残します）。

## 漏れ対策（ミュート）

FTX-1のUSBオーディオ出力には、本来の受信音ではない音声が混ざることがあります。2つのWSJT-Xを使うと、それを受信した信号のようにデコードしてしまいます。このツールは、次の3つの場合に、該当する素のチャネル（入れ替え前のLEFT/RIGHT）を無音にします。無音にするチャネルは、3つの条件の和集合です。

| 名前       | 条件                                                                                         | 無音にする素のチャネル                         | 判定の更新                  | 無効化          |
| ---------- | -------------------------------------------------------------------------------------------- | ---------------------------------------------- | --------------------------- | --------------- |
| TX         | 送信中（CATの`TX;`がTX0以外）で、送信側（＝操作バンド）が素のRIGHTにある                     | 素のLEFT（送信音声が混入する）                 | `TX;`を0.5秒間隔            | `--no-tx-mute`  |
| HF/50同士  | MAIN/SUBとも HF/50                                                                           | 素のRIGHT（操作バンド側の音声の複製、約-21dB） | `--interval`ごと            | `--no-rx-mute`  |
| CW         | MAIN・SUBのうち片側だけがCWモード（CW-U/CW-L）。両方CWのときは無音にしない               | そのバンドの音声が載っている素のチャネル       | `--interval`ごとに片側ずつ  | `--no-rx-mute`  |

- 入れ替えとミュートは、swap.txtの1行にまとめて書きます。各チャネルが何を出力するか（`L`、`R`、`0.0`＝無音）を指定します。変わるチャネルだけを書きます。例：
  - 何もなし：空
  - 入れ替えのみ：`Copy: L=R R=L`
  - 入れ替えなしで素のLEFTを無音：`Copy: L=0.0`
  - 入れ替えて、素のLEFTを無音（入れ替え後のRIGHTが無音になる）：`Copy: L=R R=0.0`
  - MAIN/SUBとも HF/50 で操作バンドがMAIN：`Copy: R=0.0`、SUB：`Copy: R=L L=0.0`
- CWの漏れは、CWでない側の音がCWの側に混ざるものなので、片側だけがCWのときに限ります。両方CWのときは無音にしないので、WSJT-XのワイドグラフでCWの信号を見られます（HF/50同士の漏れのミュートと、TXのミュートは、CWかどうかに関係なくかかります）。
- 無音にするのは、本来の受信音ではない音声が載っているチャネルだけです。送信中の他方の受信音は無線機が既にミュートしているため、失う音声はありません。
- TXの条件は、MAIN/SUBとも HF/50 のときは対象外です（素のRIGHTがHF/50同士の漏れとして常に無音になるため）。
- CWの側の判定は、CATの`MD0;`（MAIN）、`MD1;`（SUB）を1回ずつ読んで行います。1回の問い合わせで読むのは片側だけで、MAINとSUBを交互に読みます（各側は`--interval`の2倍、既定4秒ごと）。最初の1回だけ両側を読みます。送信中は読みません。モードをCWにしたときのミュートの開始と、CWをやめたときの解除は、最大でこの間隔だけ遅れます。Hamlibのモード読み出しは使いません（W-FMで失敗するため）。読み取れなかったときは、前回の判定のままにします。
- 送信状態は0.5秒間隔で読みます。ただし、漏れが起きる組み合わせ（TXの条件の「送信側がRIGHT」）のときと送信中だけです。それ以外では読みません。周波数などの判定は`--interval`の間隔で行い、送信が始まった瞬間にも更新します。バンドやVSを切り替えた直後（最大`--interval`の間）に送信を始めると、ミュートの開始が遅れることがあります。
- rigctldとの接続が切れたとき、TXのミュートは解除します（送信状態が分からなくなるため）。HF/50同士とCWのミュートは、最後の判定のまま残します。正常に終了するときは、ミュートをすべて解除します（`pythonw.exe`を強制終了した場合は戻らないので、swap.txtを空にしてください）。
- ログに、ミュートの状態が変わるたび`mute: raw L [TX]`、`mute: none`のように記録されます。
- Ver.2.1では、WSJT-Xのワイドグラフに縞状のノイズが出るのを避けるため、素のLEFTを素のRIGHTで置き換える（`Copy: L=R`）方式でした。Ver.2.2では本来のミュートである`0.0`にしています。完全なデジタル無音を入力すると、WSJT-Xのワイドグラフに縞が表示されます（実際のノイズではありません）。ワイドグラフの設定（Flattenをオフにするなど）で回避してください。ミュート中のチャネルは、デコードされなくなります。
- `--no-tx-mute`、`--no-rx-mute`は主に測定用です。ミュートを使わない運用では、漏れた音声がデコードされます。

## オプション

| オプション          | 既定値           | 内容                                                                                                                        |
| ------------------- | ---------------- | --------------------------------------------------------------------------------------------------------------------------- |
| `--mode`            | fixed            | fixed / follow / main / sub                                                                                                 |
| `--host` / `--port` | 127.0.0.1 / 4534 | rigctldの接続先                                                                                                             |
| `--interval`        | 2.0              | ポーリング間隔（秒）                                                                                                        |
| `--vuhf-mhz`        | 100              | この周波数以上をVUHFとみなす                                                                                                |
| `--swap-file`       | 自動             | swap.txtのパス。未指定時はレジストリ`HKLM\SOFTWARE\EqualizerAPO\ConfigPath`、なければ`C:\Program Files\EqualizerAPO\config` |
| `--no-tx-mute`      | -                | 送信音声の漏れ対策（TXのミュート）を使わない                                                                                |
| `--no-rx-mute`      | -                | HF/50同士の漏れとCWの側のミュートを使わない（CWの判定のためのモード読み出しも止める）                                       |
| `--dry-run`         | -                | 判定をログに出すだけでswap.txtを書かない                                                                                    |
| `--once`            | -                | 1回だけ判定して終了                                                                                                         |

## このツールの動作の詳細説明

- rigctldへは専用のTCP接続を1本張り、`\set_vfo_opt 1`（この接続だけVFO明示）にしてから、`v`・`f Main`・`f Sub`を問い合わせます。WSJT-Xなどには影響しません。
- swap.txtは判定が変わったときだけ自動的に書き換えます。
- rigctldに接続できない間は、入れ替えの状態は変えずに（TXのミュートだけ元へ戻して）再接続を試み続けます。
- CWの判定のため、2秒ごとの問い合わせのたびに、MAINまたはSUBのどちらか片側のモードをCAT（`\send_raw ; MD0;`または`MD1;`）で読みます（交互。送信中は読まない）。
- ログ: `ftx1_audio_swap.py`と同じフォルダの`logs\ftx1_audio_swap.log`（1MB×4世代でローテーション）。起動時、rigctldへの接続時、判定が変わったとき、swap.txtを書き換えたとき、ミュートの状態が変わったとき、CWの判定が変わったとき、エラーの発生時に記録します。

## ライセンス

- 本ソフトウェアは無償で配布します。個人・団体を問わず、自由に使用できます。
- ソースコードの改変、改変したものの再配布も自由です。再配布の際は、元の著作権表示を残してください。
- 本ソフトウェアは無保証です。使用によって生じたいかなる損害（オーディオ設定の変更や誤動作によるものを含む）についても、作者は責任を負いません。

「FTX-1」は八重洲無線株式会社の製品名です。本ソフトウェアは同社とは関係のない個人の制作物です。
無線機の状態の読み取りには Hamlib（rigctld）を、音声チャネルの入れ替えには Equalizer APO を使用しています。どちらも本ソフトウェアには含まれていません。

## 変更履歴

- Ver.2.2 ミュートの方式を、素のLEFTを素のRIGHTで置き換える`Copy: L=R`から、`Copy: <ch>=0.0`（無音）に戻した。MAIN/SUBとも HF/50 のときの素のRIGHT（操作バンド側の音声の漏れ）と、片側だけがCWモードのときのCWの側のチャネルも、同じ方法でミュートする（両方CWのときはミュートしない。モードはCATで片側ずつ交互に読む。`--no-rx-mute`で無効にできる）。送信状態（`TX;`）の読み出しは、ミュートが要る組み合わせのときと送信中だけ・0.5秒間隔にした（PTT遅延の軽減）WSJT-Xの受信チャネルは「Mono」ではなく「Left」「Right」に設定する旨と、構成図を「Left」に変更した。
- Ver.2.1 送信側がUSB出力の素のRIGHTにあるときだけ、送信中は素のLEFTをミュートする機能を追加した（送信音声がLEFTに混入する件の対策。`--no-tx-mute`で無効にできる）
- Ver.2.0 fixedモードを追加して既定にした（LEFT＝MAIN、RIGHT＝SUBに固定。WSJT-X #1をMAIN、#2をSUBの担当に固定する運用向け）。follow / main / sub は Ver.1.0 と同じ動作で使える
- Ver.1.0 初版

---

(c) 2026 Takeshi Mishima JK1VUZ
