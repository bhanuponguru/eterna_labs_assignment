# Token pricing updates live
This is an API and a websocket service to get realtime updates from dex screener.
## Deployed instance
I have deployed  this backend and sample frontend in an AWS EC2 instance.

frontend: http://ec2-13-232-222-222.ap-south-1.compute.amazonaws.com/eterna_labs_assignment

api: http://ec2-13-232-222-222.ap-south-1.compute.amazonaws.com:8000/docs
## technical design decisions
I have decided to use fastapi for this specific task, since it's simple to use and has great type evaluation and automatic API documentation feature, also supports web sockets.

for caching, I have decided to use redis, and for the sample frontend, i have decided to use javascript's tabulator for displaying all the data, since tabulator supports live updates, sorting and filtering.

