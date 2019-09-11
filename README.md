# A Slack bot for monitoring releases in flight

## Deployed on

* staging: mozilla-sandbox-SCIM workspace - #releaseduty channel
* prod: mozilla workspace - #releng-notifications channel

## Usage

Supported queries:

  * `shipit status`
    * Shows each active release within shipit.mozilla-releng.net. Checks only what phases have been signed off. Doesn't inspect the Taskcluster graph status within a phase
  * `shipit status $release`
    * Shows each phase signoff status and inspects the most recent phase's Taskcluster graph status. Highlighting how far along the graph is and which (if any) tasks are stuck and require attention.
    * $release: can be a substring of the full release name. e.g. 'Devedition' would match 'Devedition-70.0b5-build1'

Background tasks (non interactive):

  * every 2 min the bot will check for active releases in Shipit and ping @releaseduty if a phase's Taskcluster graph has one or more stuck tasks.

## Hacking

slackbot-release uses poetry. It's currently not packaged.

```shell
git clone https://github.com/lundjordan/slackbot-release.git
cd slackbot-release
# create your python env. e.g. with pyenv: pyenv virtualenv slackbot-release && pyenv local slackbot-release
poetry install
cp secrets.json.template secrets.json  # fill in slack token (ask jlund) and a tc client for inspecting tc groups
# connect to vpn so you can communicate with shipit
python slackbot_release/bot.py
```
