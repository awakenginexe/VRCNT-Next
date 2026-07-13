export const buildConfigFailureSettlementMeta = (setting) => {
    const statuses = setting?.failure_settlement_statuses;
    const resultValues = setting?.failure_settlement_results;

    if (
        typeof setting?.Base_Name !== "string"
        || !Array.isArray(statuses)
        || statuses.length === 0
        || !Array.isArray(resultValues)
        || resultValues.length === 0
    ) {
        return {};
    }

    return {
        failure_method_name: `updateFromBackend${setting.Base_Name}`,
        failure_statuses: [...statuses],
        failure_result_values: [...resultValues],
    };
};

export const handleConfigRouteErrorOutcome = ({
    routeMeta,
    hookResult,
    status,
    result,
    showError,
}) => {
    let settled = false;

    try {
        const acceptsStatus = routeMeta?.failure_statuses?.includes(status) === true;
        const acceptsResult = routeMeta?.failure_result_values?.includes(result) === true;
        const updateFromBackend = hookResult?.[routeMeta?.failure_method_name];

        if (acceptsStatus && acceptsResult && typeof updateFromBackend === "function") {
            updateFromBackend(result);
            settled = true;
        }
    } finally {
        if (typeof showError === "function") showError();
    }

    return settled;
};
