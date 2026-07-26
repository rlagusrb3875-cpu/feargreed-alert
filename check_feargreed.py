import requests
import os

def get_feargreed_data():
    url = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://edition.cnn.com/markets/fear-and-greed",
    }
    response = requests.get(url, headers=headers)
    print(f"상태 코드: {response.status_code}")
    print(f"응답 내용 앞부분: {response.text[:300]}")
    return response.json()

def send_telegram_alert(message):
    token = os.environ["TELEGRAM_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    requests.post(url, data={"chat_id": chat_id, "text": message})

def main():
    data = get_feargreed_data()
    fg_score = data["fear_and_greed"]["score"]
    fg_rating = data["fear_and_greed"]["rating"]
    strength_score = data["stock_price_strength"]["score"]

    alerts = []
    if fg_rating.lower() == "extreme fear":
        alerts.append(f"🚨 극한의 공포 (점수: {fg_score:.1f})")
    if strength_score <= 0:
        alerts.append(f"🚨 Stock Price Strength {strength_score:.1f}% (0% 이하)")

    if alerts:
        send_telegram_alert("\n".join(alerts))
        print("알람 전송 완료:", alerts)
    else:
        print(f"조건 미충족 - Fear&Greed: {fg_score:.1f} ({fg_rating}), Strength: {strength_score:.1f}")

if __name__ == "__main__":
    main()
