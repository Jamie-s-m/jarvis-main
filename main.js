const { app, BrowserWindow, nativeImage } = require('electron')
const path = require('path')
const { spawn } = require('child_process')

let pyProc = null
let mainWindow = null

function getPythonExecutable() {
  // Prefer .venv Python if available
  const projectRoot = path.resolve(__dirname)
  const venvPython = path.join(projectRoot, '.venv', 'Scripts', 'python.exe')
  try {
    const fs = require('fs')
    if (fs.existsSync(venvPython)) return venvPython
  } catch (e) {}
  return 'python'
}

function startPythonServer() {
  const projectRoot = path.resolve(__dirname)
  const distExe = path.join(projectRoot, 'dist', 'JarvisAgent.exe')
  const pythonExe = getPythonExecutable()
  const desktopScript = path.join(__dirname, 'jarvis_desktop.py')

  try {
    const fs = require('fs')
    if (fs.existsSync(distExe)) {
      // Prefer spawning the PyInstaller-built executable when present (recommended for end users)
      pyProc = spawn(distExe, [], { cwd: __dirname, detached: false, stdio: 'ignore' })
      pyProc.unref()
      return pyProc
    }
  } catch (e) {
    console.warn('Error checking for dist exe', e)
  }

  // Fallback to spawning python script (development)
  pyProc = spawn(pythonExe, [desktopScript], { cwd: __dirname, detached: false, stdio: 'ignore' })
  // Don't keep stdio attached to avoid blocking; we will kill process on exit.
  pyProc.unref()
  return pyProc
}

function stopPythonServer() {
  try {
    if (pyProc && !pyProc.killed) {
      pyProc.kill()
      pyProc = null
    }
  } catch (e) {
    console.warn('Failed to stop Python server:', e)
  }
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 820,
    backgroundColor: '#050812',
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false
    },
    frame: false,
    show: false,
  })

  const url = 'http://127.0.0.1:5000'
  mainWindow.loadURL(url)

  mainWindow.once('ready-to-show', () => {
    mainWindow.show()
    mainWindow.focus()
  })

  mainWindow.on('close', () => {
    stopPythonServer()
  })

  // Add system tray and global hotkeys
  try {
    const { Tray, Menu, globalShortcut } = require('electron')
    const trayIcon = path.join(__dirname, 'assets', 'tray.png')
    let tray = null
    try {
      const fs = require('fs')
      if (fs.existsSync(trayIcon)) {
        tray = new Tray(trayIcon)
      } else {
        tray = new Tray(nativeImage.createEmpty())
      }
    } catch (e) {
      tray = new Tray(nativeImage.createEmpty())
    }

    const contextMenu = Menu.buildFromTemplate([
      { label: 'Show JARVIS', click: () => { if (mainWindow) mainWindow.show(); } },
      { label: 'Toggle Listening', click: () => { toggleListening(); } },
      { label: 'Quit', click: () => { app.quit(); } }
    ])
    tray.setToolTip('JARVIS AI Agent')
    tray.setContextMenu(contextMenu)

    // Register a global hotkey (Ctrl+Shift+J) to toggle listening
    try {
      globalShortcut.register('CommandOrControl+Shift+J', () => {
        toggleListening()
      })
    } catch (e) {
      console.warn('Failed to register global shortcut', e)
    }
  } catch (e) {
    // ignore if Tray/globalShortcut not available
    console.warn('Tray or globalShortcut unavailable:', e)
  }
}

// Toggle listening by hitting the Python backend /api/toggle endpoint
function toggleListening() {
  try {
    const http = require('http')
    const data = JSON.stringify({})
    const options = {
      hostname: '127.0.0.1',
      port: 5000,
      path: '/api/toggle',
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Content-Length': data.length
      }
    }
    const req = http.request(options, (res) => {
      res.on('data', () => {})
    })
    req.on('error', (err) => { console.warn('Toggle request failed', err) })
    req.write(data)
    req.end()
  } catch (e) {
    console.warn('Failed to toggle listening:', e)
  }
}

const gotTheLock = app.requestSingleInstanceLock()
if (!gotTheLock) {
  app.quit()
} else {
  app.on('second-instance', () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore()
      mainWindow.focus()
    }
  })

  app.whenReady().then(() => {
    startPythonServer()
    // Wait a short moment for the server to come up; jarvis_desktop will itself wait and open a browser
    setTimeout(() => {
      createWindow()
    }, 1200)

    app.on('activate', function () {
      if (BrowserWindow.getAllWindows().length === 0) createWindow()
    })
  })

  app.on('window-all-closed', function () {
    if (process.platform !== 'darwin') app.quit()
  })

  app.on('quit', () => {
    stopPythonServer()
  })
}
