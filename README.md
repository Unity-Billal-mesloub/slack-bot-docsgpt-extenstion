# Slack DocsGPT Extension

This repository contains the source code for a Slack bot that leverages DocsGPT to provide intelligent responses to user queries. This bot is an extension for [DocsGPT](https://www.docsgpt.cloud/).

## Features
- Responds to user queries with intelligent answers using DocsGPT.
- Maintains conversation history in threads.
- Supports multiple storage backends for conversation history (in-memory or MongoDB).
- Easily deployable using Docker.
- Supports multiple agents.

## Prerequisites
- A Slack App with Socket Mode enabled.
- `SLACK_BOT_TOKEN` (xoxb-...) and `SLACK_APP_TOKEN` (xapp-...).
- DocsGPT API Key.

## ⚙️ Slack App Configuration (The Easy Way)

To avoid manually configuring scopes and events, use the App Manifest:

1. Go to [api.slack.com/apps](https://api.slack.com/apps).
2. Click **Create New App** and select **From an app manifest**.
3. Select your workspace and click **Next**.
4. Switch to the **YAML** tab and paste the following configuration:

   ```yaml
   display_information:
     name: DocsGPT
     description: An AI assistant powered by DocsGPT.
     background_color: "#0f172a"
   features:
     app_home:
       home_tab_enabled: false
       messages_tab_enabled: true
       messages_tab_read_only_enabled: false
     bot_user:
       display_name: DocsGPT
       always_online: true
   oauth_config:
     scopes:
       bot:
         - app_mentions:read
         - chat:write
         - im:history
   settings:
     event_subscriptions:
       bot_events:
         - app_mention
         - message.im
     socket_mode_enabled: true
    ```
5.  Click **Create**.
6.  Click **Install to Workspace** to authorize the bot.

### Where to find your tokens:

  - **SLACK\_BOT\_TOKEN (`xoxb-...`)**: Go to **OAuth & Permissions** (sidebar) -\> **Bot User OAuth Token**.
  - **SLACK\_APP\_TOKEN (`xapp-...`)**: Go to **Basic Information** (sidebar) -\> Scroll to **App-Level Tokens** -\> Click **Generate Token and Scopes** -\> Add `connections:write` -\> Generate.

## Installation

### Using Python

1. Clone the repository:
    ```bash
    git clone https://github.com/arc53/slack-bot-docsgpt-extenstion.git
    cd slack-bot-docsgpt-extenstion
    ```

2. Set up a virtual environment and install dependencies:
    ```bash
    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt
    ```

3. Create a `.env` file:
    ```plaintext
    SLACK_BOT_TOKEN=xoxb-...
    SLACK_APP_TOKEN=xapp-...
    API_KEY=<your-api-key>
    
    # Optional: Multiple Agents
    # API_KEY_SALES=<sales-agent-api-key>
    # API_KEY_SUPPORT=<support-agent-api-key>

    # Optional: Storage (Defaults to memory)
    # STORAGE_TYPE=mongodb
    ```

4. Run the bot:
    ```bash
    python bot.py
    ```

### Using Docker

1. Build and run:
    ```bash
    docker-compose up --build
    ```

## Usage

### Direct Messages
DM the bot with any question: `How do I use this?`

### Channels
Mention the bot in a channel: `@DocsBot help`

### Multiple Agents
Use `#agent` syntax:
- `@DocsBot #sales pricing?`
- DM: `#support help me`

## License
MIT License.