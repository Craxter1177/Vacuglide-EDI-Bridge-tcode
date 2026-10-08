#!/usr/bin/env python3
import asyncio
import aiohttp
import json
import websockets
import sys
import requests
import time

VACUGLIDE_DISCOVER = "https://latency.autoblowapi.com/vacuglide/connected"
VACUGLIDE_TOKEN = "ENTER YOUR TOKEN HERE!"
REQUEST_TIMEOUT = 5

SERVER_NAME = "Edi Emulator"
MESSAGE_VERSION = 3

cluster_url = None
http_session = None
last_sent_time = 0
MIN_INTERVAL = 0.20

SPEED_MULTIPLIER = 92
MIN_SPEED = 12


def discover_cluster():
    global cluster_url
    try:
        headers = {"User-Agent": "VacuglideClient/1.0", "x-device-token": VACUGLIDE_TOKEN}
        r = requests.get(VACUGLIDE_DISCOVER, headers=headers, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        cluster = r.json().get("cluster")
        if cluster and not cluster.startswith(("http://", "https://")):
            cluster = "https://" + cluster
        cluster_url = cluster.rstrip("/") if cluster else None
        print("[VacuGlide] ✅ Cluster:", cluster_url)
        return cluster_url
    except Exception as e:
        print("[VacuGlide] Discovery failed:", e)
        return None


def device_blob():
    return {
        "DeviceIndex": 0,
        "DeviceName": "Simulated Stroker",
        "DeviceDisplayName": "Simulated Stroker",
        "DeviceMessageTimingGap": 20,
        "DeviceMessages": {
            "StopDeviceCmd": {},
            "LinearCmd": [
                {
                    "FeatureDescriptor": "Stroker Position",
                    "StepCount": 100,
                    "ActuatorType": "Position"
                }
            ]
        }
    }


async def send_to_real_vacuglide(position: float, stop=False):
    global last_sent_time
    now = time.time()
    if now - last_sent_time < MIN_INTERVAL and not stop:
        return
    last_sent_time = now

    global http_session, cluster_url
    if not cluster_url or not http_session:
        return

    try:
        url = f"{cluster_url}/vacuglide/target-speed"
        
        if stop:
            payload = {"targetSpeed": 0}
            print("[VacuGlide2] ⛔ STOP")
        else:
            speed = int(MIN_SPEED + (position * SPEED_MULTIPLIER))
            speed = max(MIN_SPEED, min(100, speed))
            print(f"[DEBUG] EDI Position = {position:.3f}  →  Speed = {speed}%")
            payload = {"targetSpeed": speed}

        async with http_session.put(url, json=payload, timeout=5) as resp:
            if resp.status == 200:
                print(f"[VacuGlide2] ✅ Speed {speed}%")
            else:
                text = await resp.text()
                print(f"[VacuGlide2] ❌ {resp.status} {text[:100]}")
    except Exception as e:
        print(f"[VacuGlide2] Error: {e}")


async def handle_client(ws):
    print("[Bridge] EDI verbunden – Simulated Stroker aktiv")

    await ws.send(json.dumps([{
        "ServerInfo": {
            "Id": 1,
            "ServerName": SERVER_NAME,
            "MajorVersion": 2,
            "MinorVersion": 3,
            "BuildVersion": 0,
            "MessageVersion": MESSAGE_VERSION,
            "MaxPingTime": 0
        }
    }]))

    while True:
        try:
            text = await ws.recv()
            print("\nRAW MESSAGE:")
            print(text)
            print("------------")
            data = json.loads(text)
            wrapper = data[0]
            cmd_name = list(wrapper.keys())[0]
            inner = wrapper.get(cmd_name, {})

            if cmd_name == "LinearCmd":
                print("LINEARCMD:")
                print(json.dumps(inner, indent=2))
                try:
                    vectors = inner.get("Vectors", [])
                    for vec in vectors:
                        pos = float(vec.get("Position", 0.5))
                        duration = vec.get("Duration", 0)
                        print(
                            f"[DEBUG] REAL Position={pos:.3f} "
                            f"Duration={duration}"
                        )
                        await send_to_real_vacuglide(pos)
                except Exception as e:
                    print("[LinearCmd Error]", e)
                await ws.send(
                    json.dumps([
                        {
                            "Ok": {
                                "Id": inner.get("Id", 1)
                            }
                        }
                    ])
                )
            elif cmd_name in ("StopDeviceCmd", "StopAllDevices"):
                await send_to_real_vacuglide(0.5, stop=True)
                await ws.send(json.dumps([{"Ok": {"Id": inner.get("Id", 1)}}]))

            elif cmd_name == "RequestServerInfo":
                msg_id = inner.get("Id", 1)
                await ws.send(json.dumps([{"ServerInfo": {"Id": msg_id, "ServerName": SERVER_NAME, "MessageVersion": MESSAGE_VERSION}}]))
                await asyncio.sleep(0.5)
                await ws.send(json.dumps([{"DeviceAdded": {"Id": 0, **device_blob()}}]))

            elif cmd_name == "StartScanning":
                msg_id = inner.get("Id", 1)
                await ws.send(json.dumps([{"Ok": {"Id": msg_id}}]))
                await asyncio.sleep(0.8)
                await ws.send(json.dumps([{"DeviceAdded": {"Id": 0, **device_blob()}}]))
                await ws.send(json.dumps([{"DeviceList": {"Id": msg_id, "Devices": [device_blob()]}}]))
                await ws.send(json.dumps([{"ScanningFinished": {"Id": msg_id}}]))

            else:
                msg_id = inner.get("Id", 1)
                await ws.send(json.dumps([{"Ok": {"Id": msg_id}}]))

        except Exception as e:
            print("[Bridge] Verbindung beendet:", e)
            break


async def main():
    global http_session
    discover_cluster()

    async with aiohttp.ClientSession(headers={"x-device-token": VACUGLIDE_TOKEN}) as session:
        http_session = session
        print("[Bridge] Server läuft auf ws://127.0.0.1:54817")

        try:
            async with websockets.serve(handle_client, "127.0.0.1", 54817):
                await asyncio.Future()
        finally:
            await send_to_real_vacuglide(0.5, stop=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[Bridge] Beendet")