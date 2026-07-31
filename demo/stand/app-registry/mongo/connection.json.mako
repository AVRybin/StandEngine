<%!
import json
from urllib.parse import quote
%><%
user = cluster.preferences.admin_user
password = cluster.preferences.admin_pass
replica_set = cluster.preferences.replica_set_name
endpoint = node.private_ip
url = (
    f"mongodb://{quote(user, safe='')}:{quote(password, safe='')}@{endpoint}:27017/"
    f"?replicaSet={quote(replica_set, safe='')}&authSource=admin"
)
%>{
  "endpoint": ${json.dumps(endpoint)},
  "port": 27017,
  "credentials": {
    "user": ${json.dumps(user)},
    "password": ${json.dumps(password)},
    "authSource": "admin",
    "replicaSet": ${json.dumps(replica_set)}
  },
  "url": ${json.dumps(url)}
}
