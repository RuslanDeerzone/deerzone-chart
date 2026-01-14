$API_URL="https://melodious-courtesy-production-88f3.up.railway.app"
$ADMIN_TOKEN="0894042745ruslanvoidashenko1992"

Invoke-RestMethod `
  -Method POST `
  -Uri "$API_URL/admin/weeks/current/songs/bulk" `
  -Headers @{ "X-Admin-Token" = $ADMIN_TOKEN } `
  -ContentType "application/json" `
  -InFile ".\songs.json"