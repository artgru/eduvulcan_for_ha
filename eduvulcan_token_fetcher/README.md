# eduVULCAN Token Fetcher Add-on

This Home Assistant add-on logs into eduVULCAN using Playwright, retrieves a JWT token, decodes the tenant from the token, and writes the result to `/config/eduvulcan_token.json`. The token file can then be consumed by a custom Home Assistant integration.

## Configuration

You can provide credentials in the add-on configuration:

```yaml
login: "your_login"
password: "your_password"
```

If you leave either field blank, the add-on will prompt for credentials at runtime.

## Token storage

The token is stored at:

```
/config/eduvulcan_token.json
```

This add-on is intended to be used alongside a custom Home Assistant integration that reads the stored token.
