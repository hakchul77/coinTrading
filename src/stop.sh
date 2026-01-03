#!/bin/bash

# coin_main.py 프로세스 ID 찾기
PID=$(ps -ef | grep "[p]ython .*doge360.py" | awk '{print $2}')

if [ -z "$PID" ]; then
    echo "coin_main.py 실행 중인 프로세스가 없습니다."
    exit 0
fi

echo "종료 중... PID=$PID"
kill "$PID"

# 5초 대기 후 강제 종료 여부 확인
sleep 5
if ps -p "$PID" > /dev/null; then
    echo "프로세스가 아직 종료되지 않아 강제 종료합니다."
    kill -9 "$PID"
else
    echo "프로세스가 정상적으로 종료되었습니다."
fi
