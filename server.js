const express = require('express');
const path = require('path');

const app = express();
const PORT = process.env.PORT || 3000;

// Static files serve karne ke liye
app.use(express.static(path.join(__dirname, 'public')));

// Explicit route taaki root URL par index.html khul sake
app.get('/', (req, res) => {
    res.sendFile(path.join(__dirname, 'public', 'index.html'));
});

// Server start karna
app.listen(PORT, () => {
    console.log(`Server is running successfully on port ${PORT}`);
});
