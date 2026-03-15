# Votor

Votor is a command-line tool designed for managing and analyzing projects using vector databases.

## Features
- Initialize projects with `/init`
- Full re-indexing with `/index`
- Incremental updates with `/update`
- View index health and analytics with `/status`
- Launch an analytics dashboard with `/dashboard`
- Access AI change history with `/history`
- Undo the last AI change with `/undo`
- Revert to a specific change with `/revert <n>`
- Show diffs of changes with `/diff <n>`
- View current configuration with `/config`
- Switch AI provider or model with `/provider`
- Toggle showing retrieved sources with `/sources`
- Clear the screen with `/clear`
- Access help with `/help`
- Exit the tool with `/exit`

## Installation
To install Votor, clone the repository and install the required dependencies.

## Usage

To run Votor, follow these steps:

1. **Set up a Python virtual environment**:
   ```bash
   python -m venv venv
   ```

2. **Activate the virtual environment**:
   - On Windows:
     ```bash
     venv\Scripts\activate
     ```
   - On macOS/Linux:
     ```bash
     source venv/bin/activate
     ```

3. **Install the required dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Run Votor**:
   ```bash
   votor
   ```

Now you can use the various commands available in Votor.

qwen2.5-coder:14b

Local models that work well for sub:
qwen2.5:1.5b
