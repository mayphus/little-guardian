require('dotenv').config();
const express = require('express');
const http = require('http');
const path = require('path');
const ffmpeg = require('fluent-ffmpeg');
const onvif = require('node-onvif');

const app = express();
const server = http.createServer(app);

app.use(express.json());
app.set('view engine', 'ejs');
app.set('views', path.join(__dirname, 'views'));

const RTSP_URL = process.env.RTSP_URL;
if (!RTSP_URL) {
    console.error("RTSP_URL not set in .env");
    process.exit(1);
}

// Variables to handle PTZ
let camDevice = null;
const ONVIF_HOST = process.env.ONVIF_HOST || new URL(RTSP_URL).hostname;
const ONVIF_PORT = process.env.ONVIF_PORT || 80;
const ONVIF_USER = process.env.ONVIF_USER || new URL(RTSP_URL).username;
const ONVIF_PASS = process.env.ONVIF_PASS || new URL(RTSP_URL).password;
const PT_SPEED = 0.5;
const ZOOM_SPEED = 0.4;
const PTZ_PULSE_MS = (parseFloat(process.env.PTZ_PULSE_SECONDS) || 0.4) * 1000;

// Initialize ONVIF
async function initOnvif() {
    try {
        const device = new onvif.OnvifDevice({
            xaddr: `http://${ONVIF_HOST}:${ONVIF_PORT}/onvif/device_service`,
            user: ONVIF_USER,
            pass: ONVIF_PASS
        });
        await device.init();
        camDevice = device;
        console.log('ONVIF Camera initialized');
    } catch (err) {
        console.error('Failed to initialize ONVIF:', err.message);
    }
}
initOnvif();

// Frame status for health check
let lastFrameTime = 0;
let activeStreams = 0;
let streamStartingSince = 0;

// API Routes
app.get('/', (req, res) => {
    res.render('index');
});

// MJPEG Stream using FFmpeg
app.get('/video.mjpg', (req, res) => {
    activeStreams += 1;
    streamStartingSince = Date.now();

    res.writeHead(200, {
        'Content-Type': 'multipart/x-mixed-replace; boundary=--frame',
        'Cache-Control': 'no-cache',
        'Connection': 'close',
        'Pragma': 'no-cache'
    });

    const command = ffmpeg(RTSP_URL)
        .inputOptions([
            '-rtsp_transport', 'tcp'
        ])
        .outputOptions([
            '-f', 'image2pipe',
            '-vcodec', 'mjpeg',
            '-q:v', '5',
            '-update', '1'
        ])
        .on('start', () => {
            console.log('FFmpeg stream started');
        })
        .on('error', (err) => {
            const intentionalStop = err && /SIG(?:KILL|TERM)/.test(String(err.message || ''));
            if (intentionalStop) {
                return;
            }
            console.error('FFmpeg error:', err.message);
            res.end();
        })
        .on('end', () => {
            console.log('FFmpeg stream ended');
        });

    const ffstream = command.pipe();
    let buffer = Buffer.alloc(0);
    ffstream.on('data', (chunk) => {
        buffer = Buffer.concat([buffer, chunk]);

        while (true) {
            const soi = buffer.indexOf(Buffer.from([0xFF, 0xD8]));
            if (soi === -1) {
                // No start found, keep only the last byte in case it's the start of FF
                if (buffer.length > 0) {
                    buffer = buffer.slice(-1);
                }
                break;
            }
            if (soi > 0) {
                buffer = buffer.slice(soi);
            }

            const eoi = buffer.indexOf(Buffer.from([0xFF, 0xD9]));
            if (eoi === -1) break; // Need more data

            const frame = buffer.slice(0, eoi + 2);
            buffer = buffer.slice(eoi + 2);

            lastFrameTime = Date.now();
            streamStartingSince = 0;
            res.write(`--frame\r\nContent-Type: image/jpeg\r\nContent-Length: ${frame.length}\r\n\r\n`);
            res.write(frame);
            res.write('\r\n');
        }
    });


    req.on('close', () => {
        activeStreams = Math.max(0, activeStreams - 1);
        command.kill();
        console.log('FFmpeg stream stopped');
    });
});

// PTZ Route
app.post('/ptz', async (req, res) => {
    if (!camDevice) {
        return res.status(503).json({ ok: false, message: "Camera not initialized" });
    }

    const action = req.body.action;
    try {
        let velocity = { x: 0, y: 0, z: 0 };

        switch (action) {
            case 'up': velocity.y = PT_SPEED; break;
            case 'down': velocity.y = -PT_SPEED; break;
            case 'left': velocity.x = -PT_SPEED; break;
            case 'right': velocity.x = PT_SPEED; break;
            case 'zoom_in': velocity.z = ZOOM_SPEED; break;
            case 'zoom_out': velocity.z = -ZOOM_SPEED; break;
            case 'home':
                await camDevice.ptzGotoHomePosition();
                return res.json({ ok: true, message: "Moved home" });
            case 'stop':
                await camDevice.ptzStop();
                return res.json({ ok: true, message: "Stopped" });
            default:
                return res.status(400).json({ ok: false, message: `Unknown action ${action}` });
        }

        await camDevice.ptzMove({ speed: velocity });
        setTimeout(async () => {
            try {
                await camDevice.ptzStop();
            } catch (e) { }
        }, PTZ_PULSE_MS);

        res.json({ ok: true, message: "Moved" });
    } catch (err) {
        console.error('PTZ error:', err.message);
        res.status(500).json({ ok: false, message: "Movement failed" });
    }
});

// Health check
app.get('/health', (req, res) => {
    const now = Date.now();
    const frameAgeMs = lastFrameTime > 0 ? now - lastFrameTime : null;
    const starting = streamStartingSince > 0 && (now - streamStartingSince) < 15000;
    const frameOk = frameAgeMs !== null && frameAgeMs < 5000;
    res.json({
        frame_ok: frameOk,
        stream_starting: starting,
        active_streams: activeStreams,
        frame_age_ms: frameAgeMs,
        rtsp_host: ONVIF_HOST
    });
});

// Tracking status (simple implementation for now)
let trackingEnabled = true;

app.post('/tracking', (req, res) => {
    const { enable } = req.body;
    if (enable !== undefined) {
        trackingEnabled = !!enable;
    }
    res.json({ enabled: trackingEnabled });
});

app.get('/tracking', (req, res) => {
    res.json({ enabled: trackingEnabled });
});

const PORT = process.env.PORT || 5001;
server.listen(PORT, '0.0.0.0', () => {
    console.log(`Server running on http://0.0.0.0:${PORT}`);
});
