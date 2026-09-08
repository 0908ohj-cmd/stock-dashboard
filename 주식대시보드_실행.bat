@echo off
cd /d "%~dp0"
call venv\Scripts\activate.bat

echo [데이터 갱신 중...]
git pull origin main
echo [완료]

start "" http://localhost:8501
python -m streamlit run app.py --server.headless false
