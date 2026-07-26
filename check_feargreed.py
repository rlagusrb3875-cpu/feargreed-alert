import requests
import os

def get_feargreed_data():
    url = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
    headers = {"User-Agent": "Mozilla/5.0"}
    return requests.get(url, headers=headers).json()

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
    else:
        print(f"조건 미충족 - Fear&Greed: {fg_score:.1f} ({fg_rating}), Strength: {strength_score:.1f}")

if __name__ == "__main__":
    main() 
