@echo off
cd /d C:\Users\PC\stock-dashboard
call venv\Scripts\activate

echo [데이터 갱신 중...]
git pull origin main
echo [완료]

start http://localhost:8501
streamlit run app.py
