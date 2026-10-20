const os = require('node:os');
const path = require('node:path');

const repo = path.resolve(__dirname, '../..');
const home = process.env.COMIC_TRANSLATOR_HOME || path.join(os.homedir(), 'ComicTranslator');

module.exports = {
  apps: [{
    name: 'comictranslator',
    cwd: repo,
    script: path.join(repo, '.venv/bin/python'),
    interpreter: 'none',
    args: [path.join(repo, 'app.py'), '--no-browser', '--host', '127.0.0.1', '--port', '7873'],
    instances: 1,
    watch: false,
    autorestart: true,
    min_uptime: '10s',
    max_restarts: 10,
    restart_delay: 3000,
    kill_timeout: 15000,
    time: true,
    out_file: path.join(home, 'logs/server.out.log'),
    error_file: path.join(home, 'logs/server.err.log'),
    env: {
      COMIC_TRANSLATOR_HOME: home,
      PYTHONUNBUFFERED: '1',
    },
  }],
};
