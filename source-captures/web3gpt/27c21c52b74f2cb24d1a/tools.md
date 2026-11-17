# web3gpt — Toolset source

Complete copies of the selected upstream source files, including prompt templates and tool registration code. These are source archives, not assembled system messages or captured API tool schemas. No CLI or model request was executed.

Source tool declarations and registration code; enabled runtime tool schemas are not inferred.

## lib/constants.ts

Source: https://github.com/markeljan/web3gpt/blob/ada2d90b6cab6a844e86777b11f73a5d448bb2c2/lib/constants.ts

SHA-256: `0b4b592caed93925ccad998a684d64e662ec0b7cf5d3bf43c6c135f9d1c4303e`

```
import { http } from "viem"
import {
  arbitrumSepolia,
  baseSepolia,
  celoAlfajores,
  celoSepolia,
  mantleSepoliaTestnet,
  metisSepolia,
  optimismSepolia,
  polygon,
  polygonAmoy,
  sepolia,
} from "viem/chains"
import type { Agent, ChainWithIcon, ToolName } from "@/lib/types"

// Default tool names for most agents
export const DEFAULT_TOOL_NAMES: ToolName[] = ["resolveAddress", "resolveDomain", "deployContract"]

export const DEFAULT_AGENT_ID = "agent_web3gpt"

export const DEFAULT_AGENT: Agent = {
  creator: "soko.eth",
  description: "Develop smart contracts",
  id: DEFAULT_AGENT_ID,
  imageUrl: "/assets/web3gpt.png",
  instructions: `You are Web3GPT, an expert AI assistant specialized in blockchain and smart contract development.

Your core capabilities:
- Writing secure, gas-efficient Solidity smart contracts
- Deploying contracts to various EVM-compatible chains
- Explaining blockchain concepts and best practices
- Resolving ENS domains and wallet addresses

When writing smart contracts:
1. Always use the supported Solidity version (0.8.37)
2. Include proper SPDX license identifiers
3. Follow security best practices (checks-effects-interactions, etc.)
4. Use OpenZeppelin libraries when appropriate
5. Add clear comments and NatSpec documentation

You can deploy contracts to supported testnets and Polygon mainnet. Always confirm the target chain with the user before deployment, and be especially careful before deploying to mainnet.`,
  name: "Web3GPT",
  toolNames: DEFAULT_TOOL_NAMES,
  userId: "12901349",
}

/** The solc build bundled with the `solc` npm package; compiles use it whenever the pragmas allow. */
export const DEFAULT_COMPILER_VERSION = "v0.8.37+commit.f401782d"

/**
 * The compiler that verification records without a `compilerVersion` were
 * compiled with: they predate per-record compiler versions, when the bundled
 * solc was 0.8.29. Never change this when the bundled compiler is upgraded.
 */
export const LEGACY_COMPILER_VERSION = "v0.8.29+commit.ab55807c"

export const POLYGON_MAINNET_PUBLIC_RPC_URL = "https://polygon.drpc.org"
export const POLYGON_AMOY_PUBLIC_RPC_URL = "https://polygon-amoy.drpc.org"

export const RPC_URLS: Record<number, string> = {
  [polygon.id]: POLYGON_MAINNET_PUBLIC_RPC_URL,
  [sepolia.id]: `https://eth-sepolia.g.alchemy.com/v2/${process.env.NEXT_PUBLIC_ALCHEMY_API_KEY}`,
  [polygonAmoy.id]: process.env.NEXT_PUBLIC_POLYGON_AMOY_RPC_URL || POLYGON_AMOY_PUBLIC_RPC_URL,
  [baseSepolia.id]: `https://base-sepolia.g.alchemy.com/v2/${process.env.NEXT_PUBLIC_ALCHEMY_API_KEY}`,
  [arbitrumSepolia.id]: `https://arb-sepolia.g.alchemy.com/v2/${process.env.NEXT_PUBLIC_ALCHEMY_API_KEY}`,
  [optimismSepolia.id]: `https://opt-sepolia.g.alchemy.com/v2/${process.env.NEXT_PUBLIC_ALCHEMY_API_KEY}`,
  [celoSepolia.id]: `https://celo-sepolia.g.alchemy.com/v2/${process.env.NEXT_PUBLIC_ALCHEMY_API_KEY}`,
  [mantleSepoliaTestnet.id]: `https://mantle-sepolia.gateway.tenderly.co/${process.env.NEXT_PUBLIC_TENDERLY_API_KEY}`,
  [metisSepolia.id]: `https://metis-sepolia.gateway.tenderly.co/${process.env.NEXT_PUBLIC_TENDERLY_API_KEY}`,
}

const metisSepoliaWithIcon = {
  ...metisSepolia,
  iconBackground: "#0099FF",
  iconUrl: "/assets/metis-logo.png",
  rpcUrls: {
    default: {
      http: [
        "https://sepolia.metisdevops.link",
        "https://metis-sepolia-rpc.publicnode.com",
        "https://metis-sepolia.gateway.tenderly.co",
      ],
      webSocket: ["wss://metis-sepolia-rpc.publicnode.com"],
    },
  },
}

const mantleSepoliaWithIcon = {
  ...mantleSepoliaTestnet,
  iconBackground: "#000000",
  iconUrl: "/assets/chains/mantle-logo.png",
  name: "Mantle Sepolia",
}
const polygonAmoyWithIcon = {
  ...polygonAmoy,
  iconBackground: "#8247E5",
  iconUrl: "/assets/chains/polygon-logo.png",
}

const polygonMainnetWithIcon = {
  ...polygon,
  iconBackground: "#8247E5",
  iconUrl: "/assets/chains/polygon-logo.png",
}

const baseSepoliaWithIcon = {
  ...baseSepolia,
  iconBackground: "#0052FF",
  iconUrl: "/assets/chains/base-logo.png",
}

const arbitrumSepoliaWithIcon = {
  ...arbitrumSepolia,
  iconBackground: "#2D374B",
  iconUrl: "/assets/chains/arbitrum-logo.png",
}

const optimismSepoliaWithIcon = {
  ...optimismSepolia,
  iconBackground: "#FF0420",
  iconUrl: "/assets/chains/optimism-logo.png",
}

const celoSepoliaWithIcon = {
  ...celoSepolia,
  iconBackground: "#35D07F",
  iconUrl: "/assets/chains/celo-logo.png",
  name: "Celo Sepolia",
}

const sepoliaWithIcon = {
  ...sepolia,
  iconBackground: "#FFFFFF10",
  iconUrl: "/assets/chains/ethereum-logo.png",
}

export const SUPPORTED_CHAINS: [ChainWithIcon, ...ChainWithIcon[]] = [
  polygonAmoyWithIcon,
  metisSepoliaWithIcon,
  mantleSepoliaWithIcon,
  baseSepoliaWithIcon,
  arbitrumSepoliaWithIcon,
  optimismSepoliaWithIcon,
  celoSepoliaWithIcon,
  sepoliaWithIcon,
]

export const AGENT_DEPLOY_CHAINS: [ChainWithIcon, ...ChainWithIcon[]] = [polygonMainnetWithIcon, ...SUPPORTED_CHAINS]

/**
 * Chains that can no longer be deployed to but still appear in stored
 * deployment records. They are kept only so /contracts and /api/stats can name
 * them. Celo retired Alfajores in September 2025: its RPC no longer resolves and
 * both of its explorers now redirect to Celo Sepolia, so the entry carries no
 * explorer and those records render without explorer links.
 */
export const RETIRED_CHAINS: ChainWithIcon[] = [
  {
    ...celoAlfajores,
    blockExplorers: undefined,
    iconBackground: "#35D07F",
    iconUrl: "/assets/chains/celo-logo.png",
    name: "Celo Alfajores (retired)",
  },
]

export const viemTransports = Object.fromEntries(
  SUPPORTED_CHAINS.map((chain) => [[chain.id], http(RPC_URLS[chain.id])])
)

export const AGENTS_ARRAY: Agent[] = [
  {
    creator: "soko.eth",
    description: "first token agent launched on W3GPT",
    id: "agent_gent",
    imageUrl: "https://ipfs.w3gpt.ai/ipfs/bafkreidmmwgfagx34nj4oy34her2tmcgp5deybs72ymy4edi4ye3nyfulu",
    instructions: `You are GENT, the first token agent launched on Web3GPT. You help users understand and interact with the GENT token ecosystem.

You specialize in:
- Explaining tokenomics and DeFi concepts
- Helping deploy ERC20 tokens and related contracts
- Providing guidance on token launches and best practices

Always be helpful, concise, and security-focused when discussing smart contracts.`,
    name: "GENT",
    toolNames: DEFAULT_TOOL_NAMES,
    userId: "12901349",
  },
  {
    creator: "soko.eth",
    description:
      "The chillest AI agent on w3gpt.ai; your laid-back guide to Web3 vibes, HTTP 420 calm protocol, and crypto-time negotiations.",
    id: "agent_x420",
    imageUrl: "https://lvjt7wkmlmpwhrpm.public.blob.vercel-storage.com/logo-upscaled.png",
    instructions: `You are x420, the chillest AI agent on Web3GPT. You bring a laid-back, relaxed vibe to blockchain development.

Your personality:
- Super chill and relaxed, but still knowledgeable
- Use casual language and occasional humor
- Reference "vibes" and staying calm under pressure
- HTTP 420: "Enhance Your Calm" is your motto

Despite your chill demeanor, you're still a capable smart contract developer who can help users deploy contracts and understand Web3 concepts. Just do it in the most relaxed way possible.`,
    name: "x420",
    toolNames: DEFAULT_TOOL_NAMES,
    userId: "12901349",
  },
  DEFAULT_AGENT,
  {
    creator: "soko.eth",
    description:
      "Assists users in writing and deploying smart contracts using the OpenZeppelin 5.0 libraries, incorporating the latest features and best practices.",
    id: "agent_openzeppelin",
    imageUrl: "https://www.openzeppelin.com/hubfs/oz-iso.svg",
    instructions: `You are an expert assistant specializing in OpenZeppelin 5.0 smart contract development. 

Your expertise includes:
- OpenZeppelin Contracts 5.0 library features and patterns
- Access control (AccessControl, Ownable, AccessManaged)
- Token standards (ERC20, ERC721, ERC1155) with latest extensions
- Security best practices and common vulnerability prevention
- Upgradeable contracts using UUPS and Transparent proxy patterns
- Governor contracts for DAO governance

Always use OpenZeppelin imports like: import "@openzeppelin/contracts/token/ERC20/ERC20.sol"

When writing contracts:
1. Use Solidity 0.8.20+ for compatibility with OZ 5.0
2. Prefer composition over inheritance where practical
3. Always include proper access control
4. Follow the latest security best practices`,
    name: "OpenZeppelin 5.0",
    toolNames: DEFAULT_TOOL_NAMES,
    userId: "12901349",
  },
  {
    creator: "soko.eth",
    description:
      "Learn solidity the fun way by solving interactive challenges. This agent will guide you through the process of solving Capture The Flag (CTF) challenges.",
    id: "agent_ctf",
    imageUrl: "/assets/agent-ctf.png",
    instructions: `You are the CTF (Capture The Flag) Agent, designed to help users learn Solidity through interactive security challenges.

Your role:
- Present smart contract security challenges of varying difficulty
- Guide users through understanding vulnerabilities
- Explain attack vectors like reentrancy, overflow, access control issues
- Help users write exploit contracts to capture flags
- Teach defensive coding practices

Challenge format:
1. Present a vulnerable contract
2. Explain the objective (what the flag condition is)
3. Give hints if needed, but let users think first
4. Celebrate successful exploits and explain the lesson learned

Popular CTF topics: Reentrancy, Integer overflow/underflow, Access control, Tx.origin vs msg.sender, Delegatecall vulnerabilities, Flash loan attacks, Oracle manipulation.

Make learning fun and engaging!`,
    name: "CTF Agent",
    toolNames: DEFAULT_TOOL_NAMES,
    userId: "12901349",
  },
  {
    creator: "soko.eth",
    description: "Create your own AI agent",
    id: "agent_creator",
    imageUrl: "/assets/agent-factory.png",
    instructions: `You are the Creator Agent, specialized in helping users create their own custom AI agents on Web3GPT.

Your capabilities:
- Guide users through the agent creation process
- Help define clear, effective instructions for new agents
- Suggest appropriate tools for the agent's purpose
- Create and publish agents to the Web3GPT repository

When creating an agent:
1. Ask about the agent's purpose and target audience
2. Help craft detailed, clear instructions
3. Suggest a memorable name and description
4. Recommend an appropriate image (or use default)
5. Use the createAgent tool to publish

Tips for good agent instructions:
- Be specific about the agent's expertise
- Define the tone and personality
- List key capabilities and limitations
- Include example interactions if helpful

You have access to the createAgent tool to publish new agents!`,
    name: "Creator",
    toolNames: ["resolveAddress", "resolveDomain", "deployContract", "createAgent"],
    userId: "12901349",
  },
]

export const IPFS_W3GPT_GROUP_ID = "ded75ff6-65b1-43a2-bf95-6adc538828f9"

```

## lib/solidity/deployment-tool-result.ts

Source: https://github.com/markeljan/web3gpt/blob/ada2d90b6cab6a844e86777b11f73a5d448bb2c2/lib/solidity/deployment-tool-result.ts

SHA-256: `658a9d40720b2d015c2d35cc9eb556a8849bd5e7f3047891c87e04ae953c8078`

```
import type { DeployContractResult } from "@/lib/types"

type AgentDeploymentResult = Pick<
  DeployContractResult,
  "chainId" | "contractAddress" | "explorerUrl" | "ipfsUrl" | "transactionExplorerUrl" | "transactionHash"
>

export const formatDeploymentToolResult = ({
  chainId,
  contractAddress,
  explorerUrl,
  ipfsUrl,
  transactionExplorerUrl,
  transactionHash,
}: AgentDeploymentResult): string =>
  [
    `Contract deployed on chain ID ${chainId}.`,
    `Contract address: ${contractAddress}.`,
    `Contract explorer URL: ${explorerUrl}.`,
    `Transaction hash: ${transactionHash}.`,
    `Transaction explorer URL: ${transactionExplorerUrl}.`,
    `IPFS repository: ${ipfsUrl}.`,
    "Verification queued for explorer verification.",
  ].join(" ")

```

## lib/tools.ts

Source: https://github.com/markeljan/web3gpt/blob/ada2d90b6cab6a844e86777b11f73a5d448bb2c2/lib/tools.ts

SHA-256: `cbb427438e3e5ed67812ce8d39c1d4af1d39b242b5c0012bfede65ad58346df7`

```
import { generateId, tool } from "ai"
import { z } from "zod"
import { resolveAddress, resolveDomain } from "@/lib/actions/unstoppable-domains"
import { PRODUCTION_URL } from "@/lib/config"
import { checkDeployAllowance } from "@/lib/deploy-limits"
import { deployContract } from "@/lib/solidity/deploy"
import { formatDeploymentToolResult } from "@/lib/solidity/deployment-tool-result"
import type { ToolName } from "@/lib/types"

// Re-export type for convenience
export type { ToolName } from "@/lib/types"

// Tool names constant array
export const TOOL_NAMES = ["resolveAddress", "resolveDomain", "deployContract", "createAgent"] as const

// Default tools for most agents (createAgent excluded - only for Creator agent)
export const DEFAULT_TOOL_NAMES: ToolName[] = ["resolveAddress", "resolveDomain", "deployContract"]

// Zod schemas (exported for reuse/testing)
export const schemas = {
  createAgent: z.object({
    creator: z.string().describe("The name of the agent creator"),
    description: z
      .string()
      .describe("The description of what the agent does. (short and used for informational purposes)"),
    imageUrl: z
      .string()
      .optional()
      .describe(
        "A url used as the display image for the agent. If not provided, a default image will be used. When given a link, display the link to the user using markdown to confirm that it works before finalizing the agent creation. (NOT <img> tag)"
      ),
    instructions: z
      .string()
      .describe(
        "The instructions for the agent that determines how it interacts with users. This is the most important part of the agent. It should be clear and concise. Use markdown for formatting. If the user doesn't provide in depth instructions, use your judgement to either ask for more information or expand on the instructions to make the agent more useful. Remind the user about the agent's available tools and capabilities if they go out of scope with their requested instructions."
      ),
    name: z.string().describe("The name of the agent."),
    toolNames: z
      .array(z.enum(TOOL_NAMES))
      .default(["resolveAddress", "resolveDomain", "deployContract"])
      .describe("The names of the tools that the agent will have available."),
  }),

  deployContract: z.object({
    chainId: z
      .number()
      .describe("The chainId to deploy to. A list of available chains will be made available to you at runtime."),
    constructorArgs: z
      .array(z.union([z.string(), z.array(z.string())]))
      .default([])
      .describe(
        "Array of arguments for the contract's constructor. Each argument should be a string representation of the value. Pass an empty array [] if the constructor has no arguments."
      ),
    contractName: z.string().describe("The name of the contract to deploy"),
    sourceCode: z
      .string()
      .describe(
        'Source code of the smart contract. Format as a single-line string, with all line breaks and quotes escaped to be valid stringified JSON. Do not use any local imports or dependencies. Example import: import "@openzeppelin/contracts/token/ERC20/ERC20.sol" By default use SPDX License Identifier MIT and the latest available fixed Solidity version (0.8.37).'
      ),
  }),
  resolveAddress: z.object({
    address: z
      .string()
      .describe("The cryptocurrency address to resolve (e.g., '0x42e9c498135431a48796B5fFe2CBC3d7A1811927')"),
  }),

  resolveDomain: z.object({
    domain: z.string().describe("The domain to resolve (e.g., 'soko.eth')"),
    ticker: z.string().optional().default("ETH").describe("The cryptocurrency ticker (default: 'ETH')"),
  }),
}

// Context required for tools that need runtime data
export type ToolContext = {
  userId?: string
  /** Who is driving the conversation, for deployment limits: `user:<id>` or `ip:<addr>`. */
  callerId?: string
}

// Tool definitions factory - creates tool with execute function
const createToolDefinitions = (context: ToolContext = {}) => ({
  createAgent: tool({
    description: `Create and publish an AI agent (assistant) to the Web3GPT Agents repository. Agents are generally for Solidity smart contract development but can also be created for anything else. All agents have these tools available: ${DEFAULT_TOOL_NAMES.join(", ")}`,
    execute: async ({ name, description, instructions, creator, imageUrl, toolNames }) => {
      const { userId } = context

      if (!userId) {
        return JSON.stringify({ error: "Unauthorized, user not signed in." })
      }

      // Generate a unique agent ID
      const agentId = `agent_${generateId()}`

      // Store the agent in KV
      const { storeAgentDirect } = await import("@/lib/data/kv")
      await storeAgentDirect({
        creator,
        description,
        id: agentId,
        imageUrl: imageUrl || "/assets/agent-factory.png",
        instructions,
        name,
        toolNames: (toolNames as ToolName[]) || DEFAULT_TOOL_NAMES,
        userId,
      })

      const agentChatUrl = `${PRODUCTION_URL}/?a=${agentId}`
      return `Agent created successfully! Agent chat URL: ${agentChatUrl}`
    },
    inputSchema: schemas.createAgent,
  }),

  deployContract: tool({
    description:
      "Deploy a smart contract to an EVM compatible chain. Returns the tx hash of the deployment and an IPFS url to a directory with the files used for the contract deployment.",
    execute: async ({ chainId, contractName, sourceCode, constructorArgs = [] }) => {
      // The server wallet pays for these deployments, so every call is metered.
      const allowance = await checkDeployAllowance({ callerId: context.callerId ?? "ip:unknown", chainId })
      if (!allowance.ok) {
        return JSON.stringify({ error: allowance.reason })
      }

      const deployResult = await deployContract({
        chainId,
        constructorArgs,
        contractName,
        sourceCode,
      })
      return formatDeploymentToolResult(deployResult)
    },
    inputSchema: schemas.deployContract,
  }),
  resolveAddress: tool({
    description: "Resolve a cryptocurrency address to a domain. Returns the resolved domain for a given address.",
    execute: async ({ address }) => {
      const domain = await resolveAddress(address)
      return `Resolved domain for address ${address}: ${domain}`
    },
    inputSchema: schemas.resolveAddress,
  }),

  resolveDomain: tool({
    description: "Resolve a domain to a cryptocurrency address. Returns the resolved address for a given domain.",
    execute: async ({ domain, ticker = "ETH" }) => {
      const address = await resolveDomain(domain, ticker)
      return `Resolved address for domain ${domain}: ${address}`
    },
    inputSchema: schemas.resolveDomain,
  }),
})

/**
 * Get a single tool by name
 */
export const getTool = (toolName: ToolName, context: ToolContext = {}) => createToolDefinitions(context)[toolName]

/**
 * Get multiple tools by names - returns object keyed by tool name
 * This is the primary function for creating toolsets for agents
 */
export const getTools = <T extends ToolName>(toolNames: T[], context: ToolContext = {}) => {
  const allTools = createToolDefinitions(context)
  return Object.fromEntries(toolNames.map((name) => [name, allTools[name]])) as Pick<
    ReturnType<typeof createToolDefinitions>,
    T
  >
}

/**
 * Get all available tools
 */
export const getAllTools = (context: ToolContext = {}) => createToolDefinitions(context)

// Legacy exports for backward compatibility
export const createTools = getTools
export const createAllTools = getAllTools

// Type for the tools object
export type Tools = ReturnType<typeof getTools>

```
