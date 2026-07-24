cd /d D:\????agentharness
.\.venv\Scripts\python.exe -m pytest -q --tb=line > output\full-pytest.txt 2>&1
echo %ERRORLEVEL% > output\full-pytest.exit
