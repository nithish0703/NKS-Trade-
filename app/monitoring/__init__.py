"""
Dashboard-only trade outcome monitoring.

This package never touches strategy, scoring, risk, storage-of-signals,
or notification logic. It only watches signals the user has already
moved to "Active" via the dashboard "Trade" action and records whether
price later touched that signal's take_profit or stop_loss.
"""
