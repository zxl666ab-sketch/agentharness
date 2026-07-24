cd /d D:\????agentharness\web
call npm test > ..\output\web-test.txt 2>&1
echo %ERRORLEVEL% > ..\output\web-test.exit
