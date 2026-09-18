"""
toio コアキューブを PC の十字キーで動かす（Python 版）

必要なもの:
    pip install bleak pynput

使い方:
    python toio_keyboard.py

操作:
    ↑ / ↓        前進 / 後退
    ← / →        旋回（↑↓ と同時押しで曲がりながら前後進）
    Shift        ブースト（最高速）
    Space        停止
    [ / ]        速度を下げる / 上げる
    Esc          終了

仕組み:
    toio の BLE は 1 サービス + 用途別キャラクタリスティックというシンプルな構成。
    モーターは「時間指定つきモーター制御」(0x02) を短い有効時間で連続送信する。
    こうしておくと、プログラムが落ちてもキューブが勝手に止まるので暴走しない。
"""

import asyncio
import sys

try:
    from bleak import BleakScanner, BleakClient
except ImportError:
    sys.exit("bleak が入っていません:  pip install bleak pynput")

try:
    from pynput import keyboard
except ImportError:
    sys.exit("pynput が入っていません:  pip install bleak pynput")


# ---------------------------------------------------------------
# toio コアキューブ BLE 定義
# ---------------------------------------------------------------
SERVICE = "10b20100-5b3b-4571-9508-cf3efcd7bbae"
CHAR_ID      = "10b20101-5b3b-4571-9508-cf3efcd7bbae"  # 読み取りセンサー（位置ID）
CHAR_MOTOR   = "10b20102-5b3b-4571-9508-cf3efcd7bbae"  # モーター
CHAR_LIGHT   = "10b20103-5b3b-4571-9508-cf3efcd7bbae"  # ランプ
CHAR_SOUND   = "10b20104-5b3b-4571-9508-cf3efcd7bbae"  # サウンド
CHAR_BUTTON  = "10b20107-5b3b-4571-9508-cf3efcd7bbae"  # ボタン
CHAR_BATTERY = "10b20108-5b3b-4571-9508-cf3efcd7bbae"  # バッテリー

MAX_SPEED  = 115   # 速度指示値の上限
DEAD_ZONE  = 8     # 1〜7 は回らない
TICK       = 0.05  # 指令送信の間隔（秒）
CMD_DUR_MS = 300   # 1回の指令の有効時間（ミリ秒）
TURN_GAIN  = 0.6   # 旋回の強さ（大きいほど小回り）


# ---------------------------------------------------------------
# キー状態（pynput は別スレッドで動くが、set 操作は GIL で十分安全）
# ---------------------------------------------------------------
class KeyState:
    def __init__(self):
        self.held = set()
        self.speed = 60
        self.quit = False
        self.stop_now = False

    def on_press(self, key):
        if key == keyboard.Key.esc:
            self.quit = True
            return False
        if key == keyboard.Key.up:    self.held.add("up")
        elif key == keyboard.Key.down:  self.held.add("down")
        elif key == keyboard.Key.left:  self.held.add("left")
        elif key == keyboard.Key.right: self.held.add("right")
        elif key in (keyboard.Key.shift, keyboard.Key.shift_r): self.held.add("boost")
        elif key == keyboard.Key.space:
            self.held.clear()
            self.stop_now = True
        else:
            ch = getattr(key, "char", None)
            if ch == "[":
                self.speed = max(15, self.speed - 10)
                print(f"\r速度 {self.speed}      ", end="")
            elif ch == "]":
                self.speed = min(MAX_SPEED, self.speed + 10)
                print(f"\r速度 {self.speed}      ", end="")

    def on_release(self, key):
        if key == keyboard.Key.up:      self.held.discard("up")
        elif key == keyboard.Key.down:  self.held.discard("down")
        elif key == keyboard.Key.left:  self.held.discard("left")
        elif key == keyboard.Key.right: self.held.discard("right")
        elif key in (keyboard.Key.shift, keyboard.Key.shift_r): self.held.discard("boost")

    def wheels(self):
        """押されているキーから左右モーターの速度を求める"""
        spd = MAX_SPEED if "boost" in self.held else self.speed
        f = (1 if "up" in self.held else 0) - (1 if "down" in self.held else 0)
        t = (1 if "right" in self.held else 0) - (1 if "left" in self.held else 0)

        if f == 0 and t == 0:
            return 0.0, 0.0
        if f == 0:
            return t * spd, -t * spd                 # その場旋回

        left  = f * spd + t * spd * TURN_GAIN * f    # 内側の車輪を落として曲がる
        right = f * spd - t * spd * TURN_GAIN * f
        m = max(abs(left), abs(right))
        if m > MAX_SPEED:
            left, right = left / m * MAX_SPEED, right / m * MAX_SPEED
        return left, right


# ---------------------------------------------------------------
# モーターコマンドの組み立て
# ---------------------------------------------------------------
def motor_packet(left: float, right: float, dur_ms: int = CMD_DUR_MS) -> bytearray:
    """時間指定つきモーター制御 (0x02)"""
    def enc(v):
        direction = 0x02 if v < 0 else 0x01          # 0x01=前 / 0x02=後
        s = min(MAX_SPEED, int(round(abs(v))))
        return direction, (0 if s < DEAD_ZONE else s)

    dl, sl = enc(left)
    dr, sr = enc(right)
    dur = max(0, min(255, dur_ms // 10))             # 10ms 単位
    return bytearray([0x02, 0x01, dl, sl, 0x02, dr, sr, dur])


STOP_PACKET = bytearray([0x01, 0x01, 0x01, 0x00, 0x02, 0x01, 0x00])


# ---------------------------------------------------------------
# 通知ハンドラ
# ---------------------------------------------------------------
def on_battery(_, data: bytearray):
    print(f"\nバッテリー: {data[0]} %")


def on_button(_, data: bytearray):
    if len(data) > 1 and data[1] == 0x80:
        print("\n本体ボタンが押されました")


# ---------------------------------------------------------------
# メイン
# ---------------------------------------------------------------
async def main():
    print("toio コアキューブを探しています…（キューブを持ち上げて起こしてください）")
    device = None
    for _ in range(3):
        found = await BleakScanner.discover(timeout=5.0, service_uuids=[SERVICE])
        if not found:                                 # 環境によっては絞り込みが効かないので名前でも探す
            found = [d for d in await BleakScanner.discover(timeout=5.0)
                     if d.name and d.name.startswith("toio")]
        if found:
            device = found[0]
            break
        print("見つかりません。もう一度探します…")

    if device is None:
        sys.exit("toio コアキューブが見つかりませんでした。電源と Bluetooth を確認してください。")

    print(f"接続中: {device.name} ({device.address})")

    async with BleakClient(device) as client:
        print("接続しました\n")
        print("  ↑↓ 前後   ←→ 旋回   Shift ブースト   Space 停止   [ ] 速度   Esc 終了\n")

        # 通知を購読（対応していない機種でも落ちないように保護）
        for uuid, cb in ((CHAR_BATTERY, on_battery), (CHAR_BUTTON, on_button)):
            try:
                await client.start_notify(uuid, cb)
            except Exception:
                pass

        # 接続の合図：LED を青に、効果音を鳴らす
        await client.write_gatt_char(CHAR_LIGHT, bytearray([0x03, 0x00, 0x01, 0x01, 0x00, 0x80, 0xFF]), response=False)
        await client.write_gatt_char(CHAR_SOUND, bytearray([0x02, 0x04, 0x60]), response=False)

        state = KeyState()
        listener = keyboard.Listener(on_press=state.on_press, on_release=state.on_release)
        listener.start()

        was_moving = False
        try:
            while not state.quit:
                left, right = state.wheels()
                moving = abs(left) >= DEAD_ZONE or abs(right) >= DEAD_ZONE

                if state.stop_now:
                    state.stop_now = False
                    await client.write_gatt_char(CHAR_MOTOR, STOP_PACKET, response=False)
                    was_moving = False
                elif moving:
                    await client.write_gatt_char(CHAR_MOTOR, motor_packet(left, right), response=False)
                    was_moving = True
                    print(f"\rL {left:6.0f} / R {right:6.0f}   ", end="")
                elif was_moving:
                    await client.write_gatt_char(CHAR_MOTOR, STOP_PACKET, response=False)
                    was_moving = False
                    print("\rL      0 / R      0   ", end="")

                await asyncio.sleep(TICK)
        except KeyboardInterrupt:
            pass
        finally:
            listener.stop()
            try:
                await client.write_gatt_char(CHAR_MOTOR, STOP_PACKET, response=False)
                await client.write_gatt_char(CHAR_LIGHT, bytearray([0x01]), response=False)
            except Exception:
                pass
            print("\n終了します")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
