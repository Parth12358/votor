# Changelog
- Add template for easier future updates.
## Technical Summary of Read Mode and Write Mode

### Read Mode
In Read Mode, the system is designed to handle user queries that involve reading or displaying specific files. The process begins with the classification of the user's intent using the `classification_prompt`. If the intent is classified as "read", the sub agent is tasked with retrieving the specified files. The sub agent operates under strict rules, only calling `read_file` for each file exactly once and never guessing file paths. This ensures that the system only retrieves necessary files, optimizing resource usage.

### Write Mode
Write Mode is activated when the user's intent involves creating, editing, or modifying files. Similar to Read Mode, the process starts with intent classification. Once classified as "write", the sub agent reads the relevant files to provide context for the planned changes. The main agent then generates a detailed write plan, which includes specific edit, create, or delete actions. The sub agent's role is crucial in providing the necessary file content to inform these actions.

### Classification Flow
The classification flow is a critical component of both modes. It uses predefined prompts to determine the user's intent and the files involved. This flow ensures that the system accurately interprets user requests and allocates resources appropriately.

### Tool Execution
Tool execution is managed by the sub agent, which reads files and executes the necessary actions based on the main agent's plan. This division of labor allows for efficient processing and minimizes the risk of errors.

### Main Call Budget
The system operates within a main call budget, limiting the number of interactions between the main and sub agents. This budget is designed to optimize performance and ensure timely responses to user queries. In Write Mode, the main call budget is capped at three rounds, ensuring that the system remains efficient while providing comprehensive support for complex tasks.


