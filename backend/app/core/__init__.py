"""App-level configuration (settings), separate from database/session.py.

DATABASE_URL itself is deliberately NOT re-declared here -- it stays owned
by database.session (see that module's own docstring: "the only place
connection credentials ... may be supplied"). This package only owns
application-level settings (app name, API prefix, debug flag, CORS).
"""
