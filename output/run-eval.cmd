cd /d D:\????agentharness
mkdir output 2>nul
.\.venv\Scripts\agentharness.exe eval evals\smoke.yaml --report-json output\eval-smoke.json --report-junit output\eval-smoke.xml > output\eval-smoke-run.txt 2>&1
echo %ERRORLEVEL% > output\eval-smoke.exit
