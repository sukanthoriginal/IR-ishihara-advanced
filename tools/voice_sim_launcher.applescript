on run
	set repoPath to "/Users/sukanth/Dev/Lossfunk/IR-vOICe-simulator"
	set serverPort to "8000"
	set appURL to "http://localhost:8000/web/index.html"
	set chromeApp to "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

	-- Start the local server if it isn't already running.
	-- server.py (not plain http.server) is required: it also handles the
	-- POST /api/save-run endpoint the app uses to save each block's CSV
	-- straight into test_data/.
	set serverCheck to do shell script "lsof -ti:" & serverPort & " || true"
	if serverCheck is "" then
		do shell script "cd " & quoted form of repoPath & " && nohup python3 server.py " & serverPort & " > /tmp/voice_sim_server.log 2>&1 & disown"
		delay 1
	end if

	-- Launch Chrome as a standalone full-screen app window (no tabs/address bar)
	do shell script quoted form of chromeApp & " --app=" & quoted form of appURL & " --start-fullscreen > /tmp/voice_sim_chrome.log 2>&1 & disown"
end run
