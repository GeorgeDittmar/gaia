# Agent Guard — Design Spec

## Overview
Guardrails check to make sure any tool calls or commands run on any system is not unintentionally destructive. These guardrails will need to infer request intent against the command being run by the agent. This could be done in multiple ways:

1. Regex checks for known destructive commands
2. Clustering of commands against known destructive commands
3. Lightweight Classifier to determine if a command is potentially destructive
4. LLM based checks (slower)

### 1. Regex based checks
These would be first level checks for commands and scripts that would be run by an agent. Regex would look for low hanging fruit such as rm -rf / or other destructive commands that can harm a system if run in error.

### 2.Clustering based checks

Next level of check. Cluster embeddings of known dangerous commands or scripts to any command being run. This can be using simple knn or more complex nearest neighbor algorithms.

### 3. Light Weight Classifier 

Train / Finetune a small model to take in embeddings and classifiy as safe or not. Model should optimize for speed and accuracy. 

### 4. LLM Based Checks (Slowest but simplest)

Use a small or medium sized llm to classify if a given command is destructive or not before letting it run. This is another level of check, but could mean increased cost and latency

