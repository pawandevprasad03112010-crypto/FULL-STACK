const express = require('express');
const path = require('path');
const multer = require('multer');
const cloudinary = require('cloudinary').v2;

const app = express();
const PORT = process.env.PORT || 3000;

// Cloudinary Configuration (Aapki di gayi details)
cloudinary.config({
    cloud_name: 'zowk74pz',
    api_key: '684481677341335',
    api_secret: 'GrpVTqmhonJpsAJFm_YcY2wVnwM'
});

// Multer memory storage taaki chunk ko temporarily handle kiya ja sake
const upload = multer({ storage: multer.memoryStorage() });

app.use(express.static(path.join(__dirname, 'public')));
app.use(express.json());

// Frontend se chunk receive karke Cloudinary par upload karne ka route
app.post('/upload', upload.single('file'), async (req, res) => {
    try {
        if (!req.file) {
            return res.status(400).json({ error: 'No file uploaded' });
        }

        const uploadStream = cloudinary.uploader.upload_stream(
            { resource_type: 'video' },
            (error, result) => {
                if (error) {
                    console.error('Cloudinary upload error:', error);
                    return res.status(500).json({ error: error.message });
                }
                res.json({ secure_url: result.secure_url });
            }
        );

        uploadStream.end(req.file.buffer);
    } catch (err) {
        console.error('Server error:', err);
        res.status(500).json({ error: err.message });
    }
});

app.get('/', (req, res) => {
    res.sendFile(path.join(__dirname, 'public', 'index.html'));
});

app.listen(PORT, () => {
    console.log(`Server is running successfully on port ${PORT}`);
});
