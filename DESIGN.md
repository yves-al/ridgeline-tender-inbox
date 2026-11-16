## System's parts and roles

We're going to have code responsible for retrieving the email content from email received, at the same time it is going to retrieve other info related to that email such as the sender, their id, their domain, the email id, etc.
All that info is going to be passed to an AI judge that will be responsible for retrieving the info that we want from the email
From the AI judge response we'll decide based on the required criteria whether it needs to go through review queue or we should log a ticket for that case
Tool to check all the conditions are required based on the data received from AI judge, any unsatisfied condition puts the email into review queue
Tools (functions): we're going to use tools to log the tickets, update, or cancel based on the info that we will receive from the AI judge
Human reps will be responsible to review all inqueries in review queue