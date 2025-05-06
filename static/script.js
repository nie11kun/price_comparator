// static/script.js
document.addEventListener('DOMContentLoaded', () => {
    // --- DOM Element References ---
    const appSelect = document.getElementById('app-select');
    const planSelect = document.getElementById('plan-select');
    const priceTableBody = document.getElementById('price-table-body');
    const loadingDiv = document.getElementById('loading');
    const errorDiv = document.getElementById('error');
    const lastUpdatedDiv = document.getElementById('last-updated');

    let currentPlanList = []; // To store available plans for the selected app

    // --- Function to Populate Plan Dropdown ---
    function populatePlanOptions(prices) {
        // Extract unique plan names from the fetched price data
        const planSet = new Set();
        if (prices && Array.isArray(prices)) {
            prices.forEach(item => {
                if (item.plan_name) {
                    planSet.add(item.plan_name);
                }
                 // Simple inference for iCloud+ if plan_name is missing
                 else if (appSelect.value === 'iCloud+' && item.region === 'US') {
                     const priceMap = {0.99: '50GB', 2.99: '200GB', 9.99: '2TB', 29.99: '6TB', 59.99: '12TB'};
                     if (item.price in priceMap) {
                         planSet.add(priceMap[item.price]);
                     }
                 }
                 // Add similar inference for Google One if needed (e.g., '100 GB')
                 else if (appSelect.value === 'Google One' && item.plan_name === null && item.price === 1.99 && item.currency === 'USD') {
                     // Example, needs better logic based on actual scraped data structure
                     // planSet.add('100 GB');
                 }
            });
        }

        currentPlanList = Array.from(planSet).sort(); // Sort plans alphabetically

        // Clear existing options (except the default "-- 显示所有 --")
        planSelect.innerHTML = '<option value="">-- 显示所有 --</option>';

        // Add new options
        currentPlanList.forEach(plan => {
            const option = document.createElement('option');
            option.value = plan;
            option.textContent = plan;
            planSelect.appendChild(option);
        });

        // Show/hide plan dropdown based on whether plans were found
        planSelect.style.display = currentPlanList.length > 0 ? 'inline-block' : 'none';
        document.querySelector('.plan-label').style.display = currentPlanList.length > 0 ? 'inline-block' : 'none';

    }


    // --- Function to Fetch Data from Backend API ---
    async function fetchData() {
        const selectedApp = appSelect.value;
        const selectedPlan = planSelect.value;
        const apiUrl = `/api/prices?app=${encodeURIComponent(selectedApp)}${selectedPlan ? '&plan=' + encodeURIComponent(selectedPlan) : ''}`;

        // Update UI state: Show loading, hide error, clear table
        loadingDiv.style.display = 'block';
        errorDiv.style.display = 'none';
        errorDiv.textContent = '加载数据时出错。'; // Reset error message
        priceTableBody.innerHTML = ''; // Clear previous results
        lastUpdatedDiv.style.display = 'none';
        lastUpdatedDiv.textContent = '';

        try {
            const response = await fetch(apiUrl);

            // Check for network errors or non-OK status codes
            if (!response.ok) {
                let errorMsg = `HTTP 错误: ${response.status}`;
                try { // Try to parse error message from backend JSON response
                    const errData = await response.json();
                    errorMsg = errData.error || errorMsg;
                } catch (e) { /* Ignore if response body isn't JSON */ }
                throw new Error(errorMsg);
            }

            const data = await response.json(); // Parse JSON response

            // Populate Plan options only when "All Plans" is selected
            // This ensures the dropdown reflects plans available for the *current* app
            if (!selectedPlan) {
                 populatePlanOptions(data.prices);
             }


            // Populate Table
            if (data.prices && data.prices.length > 0) {
                data.prices.forEach(item => {
                    // Inside the data.prices.forEach loop:
                    const row = priceTableBody.insertRow();

                    let displayPlan = item.plan_name || "N/A";
                    // ... (keep existing plan inference logic if needed) ...

                    // --- Robust Price Formatting ---
                    let localPriceDisplay = 'N/A';
                    // Check if item.price exists and is potentially numeric
                    if (item.price !== null && item.price !== undefined) {
                        // Attempt to convert to number, then format
                        const priceNum = Number(item.price); // Handles both numbers and numeric strings
                        if (!isNaN(priceNum)) { // Check if conversion was successful
                            localPriceDisplay = `${priceNum.toFixed(2)} ${item.currency || ''}`;
                        } else {
                            // If it exists but isn't a number, display as is (or show error)
                            localPriceDisplay = `${item.price} ${item.currency || ''}`;
                            console.warn("item.price was not null/undefined but isNaN:", item.price);
                        }
                    }

                    let cnyPriceDisplay = 'N/A';
                    // Check if item.price_cny exists and is potentially numeric
                    if (item.price_cny !== null && item.price_cny !== undefined) {
                        const priceCnyNum = Number(item.price_cny);
                        if (!isNaN(priceCnyNum)) {
                            cnyPriceDisplay = `¥ ${priceCnyNum.toFixed(2)}`;
                        } else {
                            cnyPriceDisplay = `¥ ${item.price_cny}`; // Display as is if not a number
                            console.warn("item.price_cny was not null/undefined but isNaN:", item.price_cny);
                        }
                    }
                    // --- End Robust Price Formatting ---


                    row.insertCell().textContent = item.region || 'N/A';
                    row.insertCell().textContent = displayPlan;
                    row.insertCell().textContent = localPriceDisplay; // Use formatted string
                    row.insertCell().textContent = cnyPriceDisplay; // Use formatted string
                });

                // Display Last Updated Time
                if (data.last_updated && data.last_updated !== "N/A" && data.last_updated !== "Never") {
                     try {
                        lastUpdatedDiv.textContent = `数据更新于: ${new Date(data.last_updated).toLocaleString()}`;
                        lastUpdatedDiv.style.display = 'block';
                     } catch (e) {
                         lastUpdatedDiv.textContent = `数据更新时间戳格式无法解析: ${data.last_updated}`;
                         lastUpdatedDiv.style.display = 'block';
                     }
                } else {
                    lastUpdatedDiv.textContent = '数据更新时间未知。';
                    lastUpdatedDiv.style.display = 'block';
                }


            } else {
                // Display message if no price data found
                const row = priceTableBody.insertRow();
                const cell = row.insertCell();
                cell.colSpan = 4; // Adjust colspan if you add/remove columns
                cell.textContent = '未找到符合条件的价格信息。服务可能在此地区不可用或暂无数据。';
                cell.style.textAlign = 'center';
                cell.style.padding = '20px';
                cell.style.color = '#6c757d';
            }

        } catch (error) {
            console.error('Fetch error:', error);
            errorDiv.textContent = `加载数据时出错: ${error.message}`; // Display specific error
            errorDiv.style.display = 'block'; // Show error message
        } finally {
            loadingDiv.style.display = 'none'; // Hide loading indicator regardless of success/failure
        }
    }

    // --- Event Listeners ---
    appSelect.addEventListener('change', () => {
        // Reset plan dropdown when app changes *before* fetching
        planSelect.innerHTML = '<option value="">-- 显示所有 --</option>';
        // Fetch data for the newly selected app (will populate plans if needed)
        fetchData();
    });

    planSelect.addEventListener('change', fetchData); // Fetch data when plan selection changes


    // --- Initial Data Load ---
    fetchData(); // Load data for the default selected app (iCloud+) on page load

}); // End DOMContentLoaded