const solc=require('solc'),fs=require('fs');
const src=fs.readFileSync('contracts/Testbed.sol','utf8');
const input={language:'Solidity',sources:{'Testbed.sol':{content:src}},settings:{optimizer:{enabled:true,runs:200},evmVersion:'cancun',outputSelection:{'*':{'*':['abi','evm.bytecode.object']}}}};
const out=JSON.parse(solc.compile(JSON.stringify(input)));
let bad=false;(out.errors||[]).forEach(e=>{console.error(e.formattedMessage);if(e.severity==='error')bad=true});
if(bad)process.exit(1);
const art={};for(const [n,c] of Object.entries(out.contracts['Testbed.sol']))art[n]={abi:c.abi,bytecode:'0x'+c.evm.bytecode.object};
fs.mkdirSync('build',{recursive:true});fs.writeFileSync('build/artifacts.json',JSON.stringify(art));
console.log('compiled',Object.keys(art).join(','));
