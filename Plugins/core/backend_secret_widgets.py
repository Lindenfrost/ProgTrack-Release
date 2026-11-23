# Part of: ProgTrack 0.3.0
# Required ProgTrack version: 0.3.0 or newer.
"""Show saved credential state without putting it in the input value."""

from PyQt6.QtWidgets import QLineEdit


class SavedSecretEdit(QLineEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setEchoMode(QLineEdit.EchoMode.Password)

    def set_saved(self, present: bool, explanation: str) -> None:
        """Set the placeholder and help text, preserving any drafted input."""
        self.setPlaceholderText("********" if present else "")
        help_text = explanation if present else ""
        self.setToolTip(help_text)
        self.setAccessibleDescription(help_text)
