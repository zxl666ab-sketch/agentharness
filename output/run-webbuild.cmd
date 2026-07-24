cd /d D:\????agentharness\web
call npm run build > ..\output\web-build.txt 2>&1
echo %ERRORLEVEL% > ..\output\web-build.exit
