import os
import py_clob_client

lib_path = os.path.dirname(py_clob_client.__file__)
print(f"Searching for 'get_balance' in {lib_path}")

matches = []
for root, dirs, files in os.walk(lib_path):
    for file in files:
        if file.endswith(".py"):
            path = os.path.join(root, file)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
                    if "def get_balance" in content:
                        print(f"Found in: {path}")
                    if "def get_positions" in content:
                        print(f"Found positions in: {path}")
            except:
                pass
