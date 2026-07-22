<%!
import json
from urllib.parse import quote
%><%
user = cluster.preferences.admin_user
password = cluster.preferences.admin_pass
endpoint = node.private_ip
url = f"redis://{quote(user, safe='')}:{quote(password, safe='')}@{endpoint}:6379"
%>{
  "endpoint": ${json.dumps(endpoint)},
  "port": 6379,
  "credentials": {
    "user": ${json.dumps(user)},
    "password": ${json.dumps(password)}
  },
  "url": ${json.dumps(url)}
}
