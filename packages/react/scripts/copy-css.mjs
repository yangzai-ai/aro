import {mkdirSync,copyFileSync} from 'node:fs';
mkdirSync('dist/styles',{recursive:true});
copyFileSync('src/styles/default.css','dist/styles/default.css');
