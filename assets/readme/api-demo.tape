# The HTTP API end to end against `docker compose up`: queue a job, poll it, read the result.
# Re-record: docker compose up -d --build && vhs assets/readme/api-demo.tape
Output assets/readme/api-demo.gif

Require curl
Require jq

Set Shell "bash"
Set FontSize 15
Set Width 1100
Set Height 620
Set Padding 18
Set Theme "Dracula"
Set TypingSpeed 25ms

Hide
Type `export PS1='$ ' Q='{"query": "портативный ирригатор IPX7 до 38000", "sources": ["kaspi"]}'` Enter
Type "clear" Enter
Show

Type `curl -s -X POST localhost:8000/recommend -H 'content-type: application/json' -d "$Q" | tee /tmp/job.json | jq`
Enter
Sleep 2.5s
Type "ID=$(jq -r .job_id /tmp/job.json); curl -s localhost:8000/recommend/$ID | jq .status"
Enter
Sleep 2.5s

# The run takes ~45 s; skip the wait.
Hide
Type `until curl -s localhost:8000/recommend/$ID | jq -e '.status | test("done|no_match|failed")' >/dev/null; do sleep 2; done; clear` Enter
Wait@120s /^\$$/
Show

Type "curl -s localhost:8000/recommend/$ID | jq '{status, best: .result.best | {title, price, score, source}}'"
Enter
Sleep 3.5s
Type "curl -s localhost:8000/recommend/$ID | jq -r .result.report_markdown | sed -n '1,16p'"
Enter
Sleep 6s
