import datetime, os, requests
from dotenv import load_dotenv
load_dotenv()

discord_url = os.getenv("DISCORD_URL")

# 디스코드 채널로 메세지 전송
def discord_send_message(text):
    now = datetime.datetime.now()
    # message = {"content": f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] {str(text)}"}
    message = {"content": f"{str(text)}"}
    requests.post(discord_url, data=message)
    # print(message)

if __name__ == "__main__":
    discord_send_message("TEST")