const express = require('express');
const path = require('path');

const app = express();
const PORT = process.env.PORT || 3000;

// 'public' folder ko static serve karne ke liye
app.use(express.static(path.join(__dirname, 'public')));

// Server start karna
app.listen(PORT, () => {
    console.log(`Server is running successfully on port ${PORT}`);
});
