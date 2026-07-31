<%!
import json
%><%
user = cluster.preferences.admin_user
password = cluster.preferences.admin_pass
endpoint = node.private_ip
url = f"http://{endpoint}:8180"
%>{
  "endpoint": ${json.dumps(endpoint)},
  "port": 8180,
  "credentials": {
    "user": ${json.dumps(user)},
    "password": ${json.dumps(password)}
  },
  "url": ${json.dumps(url)}
}
