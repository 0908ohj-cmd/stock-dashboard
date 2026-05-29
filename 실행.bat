@echo off
cd /d C:\Users\PC\stock-dashboard
call venv\Scripts\activate
start http://localhost:8501
streamlit run app.py
