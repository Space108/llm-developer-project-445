# Automatic tests

After completing all the steps in the project, automatic tests will become available to you. Tests are run on each commit - once all tasks in the Hexlet interface are completed, make a commit, and the tests will run automatically.

The hexlet-check.yml file is responsible for running these tests - do not delete this file, edit it, or rename the repository.

Дополнительно (наш контур, не Хекслет): `ci.yml` гоняет `check_docs --strict` и `smoke G=7` без стенда и без LLM. Файл `hexlet-check.yml` не меняем.
