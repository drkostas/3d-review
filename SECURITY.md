# Security

Please report a security problem privately through GitHub's "Report a vulnerability" button on this repository, not in a public issue.

- The bench has no login. Anyone who can reach its port can read the threads and add submissions. Bind it to `127.0.0.1` (the example settings do) or serve it only on a private network.
- The Penpot bridge reads credentials from a settings file or the environment. Keep that file readable only by you, and prefer an access token to a password.
