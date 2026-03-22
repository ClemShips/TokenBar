from setuptools import setup

APP = ["tokenbar.py"]
OPTIONS = {
    "argv_emulation": False,
    "iconfile": "TokenBar.icns",
    "plist": {
        "LSUIElement": True,
        "CFBundleName": "TokenBar",
        "CFBundleDisplayName": "TokenBar",
        "CFBundleIdentifier": "com.clement.tokenbar",
        "CFBundleVersion": "1.0.0",
        "CFBundleShortVersionString": "1.0",
        "NSHighResolutionCapable": True,
    },
    "packages": ["WebKit"],
    "resources": ["ui"],
    "frameworks": [],
}

setup(
    app=APP,
    name="TokenBar",
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
