#!/bin/bash

echo ""
echo "==========================================================================="
echo "  Sample AWS IDP Pipeline - Automated Destroy"
echo "---------------------------------------------------------------------------"
echo "  Destroys all IDP pipeline resources via CodeBuild."
echo ""
echo "  Stacks: Application, Agent, Mcp, Worker, Websocket, Workflow,"
echo "          Transcribe, Bda, Ocr, Event, Storage"
echo "==========================================================================="
echo ""

# Default parameters
REPO_URL="https://github.com/prasoongupta925/dsa-document-ai.git"
VERSION="main"
STACK_NAME="sample-aws-idp-pipeline-destroy-codebuild"
TEMPLATE_URL_BASE=""
TEMPLATE_FILE="/tmp/destroy-codebuild.yml"
CDK_CONTEXT_ARGS=""

# Parse command-line arguments
while [[ "$#" -gt 0 ]]; do
    case $1 in
        --repo-url) REPO_URL="$2"; shift ;;
        --version) VERSION="$2"; shift ;;
        --stack-name) STACK_NAME="$2"; shift ;;
        --template-url-base) TEMPLATE_URL_BASE="$2"; shift ;;
        --context)
            if [[ ! "$2" =~ ^[A-Za-z][A-Za-z0-9:._-]*=[A-Za-z0-9:._/-]*$ ]]; then
                echo "Invalid --context value: '$2' (expected KEY=VALUE, e.g. lancedbExpressAzId=aps1-az3)"
                exit 1
            fi
            CDK_CONTEXT_ARGS="${CDK_CONTEXT_ARGS:+$CDK_CONTEXT_ARGS }-c $2"; shift ;;
        --help)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --repo-url URL        Repository URL (default: github.com/prasoongupta925/dsa-document-ai)"
            echo "  --version VERSION     Branch or tag (default: main)"
            echo "  --stack-name NAME     CloudFormation stack name (default: sample-aws-idp-pipeline-destroy-codebuild)"
            echo "  --template-url-base URL  Raw base URL of the CodeBuild template (default: derived from --repo-url)"
            echo "  --context KEY=VALUE   CDK context for the build, repeatable (e.g. lancedbExpressAzId=aps1-az3)"
            echo "  --help                Show this help message"
            exit 0
            ;;
        *) echo "Unknown parameter: $1"; exit 1 ;;
    esac
    shift
done

# The CodeBuild template comes from the repository being deployed (--repo-url
# at --version), so a fork's own template changes are used, not upstream's.
# For a repository outside GitHub, pass --template-url-base.
if [[ -z "$TEMPLATE_URL_BASE" ]]; then
    REPO_PATH="${REPO_URL%/}"
    REPO_PATH="${REPO_PATH%.git}"
    if [[ "$REPO_PATH" =~ ^https://github\.com/([^/]+/[^/]+)$ ]]; then
        TEMPLATE_URL_BASE="https://raw.githubusercontent.com/${BASH_REMATCH[1]}"
    else
        echo "Cannot derive the template URL from $REPO_URL; pass --template-url-base URL."
        exit 1
    fi
fi
TEMPLATE_URL="${TEMPLATE_URL_BASE%/}/${VERSION}/destroy-codebuild.yml"

# Display configuration
echo "Configuration:"
echo "--------------"
echo "Repository:  $REPO_URL"
echo "Version:     $VERSION"
echo "Stack Name:  $STACK_NAME"
echo "Template:    $TEMPLATE_URL"
if [[ -n "$CDK_CONTEXT_ARGS" ]]; then
echo "CDK context: $CDK_CONTEXT_ARGS"
fi
echo ""

# Confirm destroy
echo "WARNING: This will permanently delete all IDP pipeline resources."
echo ""
while true; do
    read -p "Do you want to proceed with destroy? (y/N): " answer
    case ${answer:0:1} in
        y|Y ) break ;;
        n|N|"" ) echo "Destroy cancelled."; exit 0 ;;
        * ) echo "Please enter y or n." ;;
    esac
done

# Download CloudFormation template
echo ""
echo "Downloading CloudFormation template..."
echo "  $TEMPLATE_URL"
curl -fsSL -o "$TEMPLATE_FILE" "$TEMPLATE_URL"
if [[ $? -ne 0 ]]; then
    echo "Failed to download template from $TEMPLATE_URL"
    exit 1
fi

# Validate template
echo "Validating CloudFormation template..."
aws cloudformation validate-template --template-body "file://$TEMPLATE_FILE" > /dev/null 2>&1
if [[ $? -ne 0 ]]; then
    echo "Template validation failed."
    exit 1
fi

# Deploy CloudFormation stack
echo "Deploying destroy CodeBuild stack..."
aws cloudformation deploy \
    --stack-name "$STACK_NAME" \
    --template-file "$TEMPLATE_FILE" \
    --capabilities CAPABILITY_IAM \
    --parameter-overrides \
        RepoUrl="$REPO_URL" \
        Version="$VERSION"

if [[ $? -ne 0 ]]; then
    echo "CloudFormation deployment failed."
    exit 1
fi

echo "Waiting for stack to complete..."
spin='-\|/'
i=0
while true; do
    status=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --query 'Stacks[0].StackStatus' --output text 2>/dev/null)
    if [[ "$status" == "CREATE_COMPLETE" || "$status" == "UPDATE_COMPLETE" ]]; then
        break
    elif [[ "$status" == *"FAILED"* || "$status" == *"ROLLBACK"* ]]; then
        echo ""
        echo "Stack failed with status: $status"
        exit 1
    fi
    printf "\r${spin:i++%${#spin}:1}"
    sleep 1
done
echo -e "\nStack deployed successfully."

# Get CodeBuild project name
PROJECT_NAME=$(aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --query 'Stacks[0].Outputs[?OutputKey==`ProjectName`].OutputValue' \
    --output text)

if [[ -z "$PROJECT_NAME" ]]; then
    echo "Failed to retrieve CodeBuild project name."
    exit 1
fi

# Start CodeBuild
echo ""
echo "Starting CodeBuild: $PROJECT_NAME ..."
# Same 6 GB Node heap as deploy.sh: the destroy build runs the same
# compile/bundle step that ran out of memory with the default heap.
CODEBUILD_ENV_OVERRIDES="[{\"name\":\"NODE_OPTIONS\",\"value\":\"--max-old-space-size=6144\",\"type\":\"PLAINTEXT\"}"
if [[ -n "$CDK_CONTEXT_ARGS" ]]; then
    CODEBUILD_ENV_OVERRIDES+=",{\"name\":\"CDK_CONTEXT_ARGS\",\"value\":\"$CDK_CONTEXT_ARGS\",\"type\":\"PLAINTEXT\"}"
fi
CODEBUILD_ENV_OVERRIDES+="]"
BUILD_ID=$(aws codebuild start-build --project-name "$PROJECT_NAME" --environment-variables-override "$CODEBUILD_ENV_OVERRIDES" --query 'build.id' --output text)

if [[ -z "$BUILD_ID" ]]; then
    echo "Failed to start CodeBuild."
    exit 1
fi

echo "Build ID: $BUILD_ID"
echo ""
echo "You can monitor progress in the AWS Console:"
echo "  CodeBuild > Build projects > $PROJECT_NAME"
echo ""

# Wait for build
while true; do
    BUILD_STATUS=$(aws codebuild batch-get-builds --ids "$BUILD_ID" --query 'builds[0].buildStatus' --output text)
    PHASE=$(aws codebuild batch-get-builds --ids "$BUILD_ID" --query 'builds[0].phases[?phaseStatus==`IN_PROGRESS`].phaseType' --output text)

    if [[ -n "$PHASE" ]]; then
        echo -ne "\rPhase: $PHASE          "
    fi

    if [[ "$BUILD_STATUS" == "SUCCEEDED" || "$BUILD_STATUS" == "FAILED" || "$BUILD_STATUS" == "STOPPED" ]]; then
        echo ""
        break
    fi
    sleep 10
done

echo ""
echo "Build completed: $BUILD_STATUS"

if [[ "$BUILD_STATUS" != "SUCCEEDED" ]]; then
    BUILD_DETAIL=$(aws codebuild batch-get-builds --ids "$BUILD_ID" --query 'builds[0].logs.{groupName: groupName, streamName: streamName}' --output json)
    LOG_GROUP=$(echo "$BUILD_DETAIL" | jq -r '.groupName')

    echo ""
    echo "Destroy failed. For logs:"
    echo "  aws logs tail $LOG_GROUP --since 10m"
    exit 1
fi

# Self-cleanup: delete the destroy CodeBuild stack
echo ""
echo "Cleaning up destroy CodeBuild stack..."
aws cloudformation delete-stack --stack-name "$STACK_NAME"

echo ""
echo "==========================================================================="
echo "  Destroy Completed"
echo "==========================================================================="
echo ""
echo "  All IDP pipeline resources have been removed."
echo ""
echo "  Note: CDKToolkit stack is preserved for future deployments."
echo "        To remove it: aws cloudformation delete-stack --stack-name CDKToolkit"
echo ""
echo "==========================================================================="
