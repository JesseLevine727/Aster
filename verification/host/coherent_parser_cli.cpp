#include "../common/coherent_record.h"
#include <iostream>
int main() {
    std::string length;
    while (std::getline(std::cin, length)) {
        const auto size = std::stoul(length);
        if (size > 65536) return 1;
        std::string input(size, '\0'); std::cin.read(input.data(), size);
        if (!std::cin) return 1;
        try { validate_coherent_record(input); std::cout << "PASS\n"; }
        catch (const std::exception&) { std::cout << "FAIL\n"; }
    }
}
